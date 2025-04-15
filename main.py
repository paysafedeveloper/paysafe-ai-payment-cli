import argparse
import json
import time
import uuid
import requests
import threading
import tempfile
import traceback
from rich.console import Console
from rich.table import Table
from rich.progress import track
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

PAYSAFE_API_BASE = "https://api.test.paysafe.com/paymenthub/v1"

def load_env(env_path):
    with open(env_path, 'r') as f:
        env_data = json.load(f)
        return {item['key']: item['value'] for item in env_data['values'] if item['enabled']}

def load_expected_responses():
    try:
        with open("expect_response.json", 'r') as f:
            data = json.load(f)
            return {str(item["error_code"]): item["response"] for item in data if "error_code" in item and "response" in item}
    except FileNotFoundError:
        console.print("[red]expect_response.json not found. Skipping expected code check.[/red]")
        return {}

def generate_merchant_ref():
    return str(uuid.uuid4())

def auth_header(key):
    return {
        "Authorization": f"Basic {key}",
        "Accept": "application/json"
    }

payment_status_shared = {"payment_id": None}

def cancel_payment_if_needed_threadsafe(private_key):
    while payment_status_shared["payment_id"] is None:
        time.sleep(0.2)
    payment_id = payment_status_shared["payment_id"]
    cancel_url = f"https://api.test.paysafe.com/paymenthub/v1/payments/{payment_id}"
    headers = auth_header(private_key)
    headers.update({"Content-Type": "application/json", "Simulator": "INTERNAL"})
    payload = {"status": "CANCELLED"}
    response = requests.put(cancel_url, headers=headers, json=payload)
    if response.ok:
        data = response.json()
        console.print(f"[blue]Cancellation response:[/blue] {data['status']}")
    else:
        console.print("[red]Failed to cancel payment.[/red]")
        console.print(response.text)

def perform_settlement(payment_id, amount, private_key, merchant_ref):
    url = f"https://api.test.paysafe.com/paymenthub/v1/payments/{payment_id}/settlements"
    payload = {
        "merchantRefNum": merchant_ref,
        "dupCheck": True,
        "amount": amount
    }
    headers = auth_header(private_key)
    headers.update({"Content-Type": "application/json", "Simulator": "INTERNAL"})

    response = post_with_logging(url, headers, payload)
    data = response

    console.print(Panel.fit(f"[bold cyan]Settlement Response:[/bold cyan]\n"
                            f"ID: {data.get('id')}\n"
                            f"Status: {data.get('status')}\n"
                            f"Txn Time: {data.get('txnTime')}\n"
                            f"Amount: {data.get('amount')}\n"
                            f"Available to Refund: {data.get('availableToRefund')}", title="Settlement"))

    if data.get("status") == "PENDING" or data.get("availableToRefund", 0) < amount:
        console.print("[yellow]Settlement is PENDING or amount mismatch — attempting to cancel payment.[/yellow]")
        cancel_payment_if_needed_threadsafe(private_key)

    return data.get("id")



def attempt_refund(settlement_id, amount, merchant_ref, currency, private_key):
    console.print("[bold]7. Initiating Refund...[/bold]")
    url = f"https://api.test.paysafe.com/paymenthub/v1/settlements/{settlement_id}/refunds"
    headers = auth_header(private_key)
    headers.update({"Content-Type": "application/json", "Simulator": "INTERNAL"})
    payload = {
        "merchantRefNum": merchant_ref,
        "amount": amount,
        "dupCheck": True
    }
    refund = post_with_logging(url, headers, payload)
    refund_id = refund['id']
    console.print(f"[green]Refund Submitted. ID:[/green] {refund_id}")
    for _ in range(10):
        status = get_with_logging(f"https://api.test.paysafe.com/paymenthub/v1/refunds/{refund_id}", auth_header(private_key))
        if status['status'] == 'COMPLETED':
            console.print(f"[bold green]Refund Completed[/bold green] ✅")
            return
        time.sleep(2)
    console.print("[yellow]Refund still processing or failed after retries.[/yellow]")

def submit_payment_and_poll(handle_token, merchant_ref, amount, currency, private_key, refund_flag):
    payment_payload = {
        "merchantRefNum": merchant_ref,
        "amount": amount,
        "currencyCode": currency,
        "paymentHandleToken": handle_token
    }
    payment = post_with_logging("https://api.test.paysafe.com/paymenthub/v1/payments", auth_header(private_key), payment_payload)
    payment_id = payment['id']
    payment_status_shared["payment_id"] = payment_id
    console.print(f"[green]Payment Submitted. ID:[/green] {payment_id}")

    console.print("[bold]5. Polling for Payment Completion...[/bold]")
    for _ in track(range(10), description="Checking payment status..."):
        status = get_with_logging(f"https://api.test.paysafe.com/paymenthub/v1/payments/{payment_id}", auth_header(private_key))
        if status['status'] == 'COMPLETED':
            console.print(f"[bold green]Payment Completed[/bold green] ✅")
            # Always perform settlement
            settlement_id = perform_settlement(payment_id, amount, private_key, merchant_ref)
            if refund_flag and settlement_id:
                attempt_refund(settlement_id, amount, merchant_ref, currency, private_key)
            break
        time.sleep(2)
    else:
        console.print(f"[bold red]Payment not completed in time.[/bold red]")


def display_payment_methods(payment_methods):
    table = Table(title="Available Payment Methods")
    table.add_column("Method")
    table.add_column("Processor")
    table.add_column("Account")
    table.add_column("MCC Description")
    table.add_column("ApplePay")
    table.add_column("GooglePay")
    table.add_column("Wallet")

    for method in payment_methods:
        acct_cfg = method.get("accountConfiguration", {})
        table.add_row(
            method.get("paymentMethod", "-"),
            method.get("processorCode", "-"),
            method.get("accountId", "-"),
            method.get("mccDescription", "-"),
            str(acct_cfg.get("isApplePay", False)),
            str(acct_cfg.get("isGooglePay", False)),
            str(acct_cfg.get("isCustomerWalletEnabled", False))
        )
    console.print(table)

def prompt_card_details():
    console.print("[bold blue]Enter Card Details[/bold blue]")
    card_number = Prompt.ask("Card Number", default="4000000000002503")
    expiry_month = Prompt.ask("Expiry Month (MM)", default="02")
    expiry_year = Prompt.ask("Expiry Year (YYYY)", default="2026")
    cvv = Prompt.ask("CVV", default="111")
    holder_name = Prompt.ask("Cardholder Name", default="John Doe")
    return {
        "cardNum": card_number,
        "cardExpiry": {"month": expiry_month, "year": expiry_year},
        "cvv": int(cvv),
        "holderName": holder_name
    }

def prompt_billing_address():
    console.print("[bold blue]Enter Billing Address[/bold blue]")
    street = Prompt.ask("Street", default="5335 Gate Pkwy")
    city = Prompt.ask("City", default="Jacksonville")
    zip_code = Prompt.ask("ZIP", default="32256")
    state = Prompt.ask("State", default="FL")
    country = Prompt.ask("Country", default="US")
    return {
        "nickName": "Home",
        "street": street,
        "city": city,
        "zip": zip_code,
        "country": country,
        "state": state
    }

def prompt_profile():
    console.print("[bold blue]Enter Customer Profile[/bold blue]")
    first_name = Prompt.ask("First Name", default="John")
    last_name = Prompt.ask("Last Name", default="Doe")
    email = Prompt.ask("Email", default="john.doe@paysafe.com")
    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": email
    }

def prompt_amount():
    dollars = Prompt.ask("Enter donation amount in dollars", default="5")
    try:
        amount_minor = int(float(dollars) * 100)
        return amount_minor
    except ValueError:
        console.print("[red]Invalid input. Must be a number.")
        exit(1)

def enrich_payload(payload):
    payload["card"] = prompt_card_details()
    payload["billingDetails"] = prompt_billing_address()
    payload["profile"] = prompt_profile()
    return payload


def post_with_logging(url, headers, payload):
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        try:
            err_json = response.json()
            code = str(err_json.get("error", {}).get("code", "N/A"))
            message = err_json.get("error", {}).get("message", "")
            additional = err_json.get("error", {}).get("additionalDetails", [])
            details = "\n".join([f"[bold cyan]{d.get('type')}[/bold cyan] ({d.get('code')}): {d.get('message')}" for d in additional])
            summary = f"[red]Error Code:[/red] {code}\n[red]Message:[/red] {message}\n{details}"

            expected_map = load_expected_responses()
            expected_info = expected_map.get(code)
            if expected_info:
                console.print(Panel.fit(f"Expected: [green]{expected_info}[/green]\nActual: [cyan]{message}[/cyan]", title="[blue]Error Code Validation[/blue]"))
            else:
                console.print(f"[yellow]No expectation found for error code {code}[/yellow]")

            if any(d.get("code") == "ADVICE-06" for d in additional):
                console.print(Panel.fit(summary, title="[yellow]Important Advisory[/yellow]", subtitle="This appears to be a step-up or bank referral situation."))
            else:
                console.print(Panel.fit(summary, title="[red]Payment Failed[/red]"))
        except Exception:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode='w') as tmp:
                traceback.print_exc(file=tmp)
                console.print(Panel.fit("Workflow failed at payment step. See trace log for details.", title="[red]Fatal Error[/red]"))
                console.print(f"[dim]Traceback saved to:[/dim] {tmp.name}\n[bold yellow]To view details:[/bold yellow] [italic]cat {tmp.name}[/italic]")
        raise

def get_with_logging(url, headers):
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode='w') as tmp:
            traceback.print_exc(file=tmp)
            console.print(Panel.fit("Workflow failed while making GET request.", title="[red]Fatal Error[/red]"))
            console.print(f"[dim]Traceback saved to:[/dim] {tmp.name}\n[bold yellow]To view details:[/bold yellow] [italic]cat {tmp.name}[/italic]")
        raise

def run_test(env, currency, amount, refund_flag, cancel_flag, interactive_flag):
    if not interactive_flag and amount is None:
        console.print("[red]Amount must be provided unless --interactive is enabled.[/red]")
        exit(1)
    if interactive_flag and amount is None:
        amount = prompt_amount()

    expected_map = load_expected_responses()
    expected = expected_map.get(str(amount))
    if expected:
        console.print(Panel.fit(f"Simulated Amount: [bold cyan]{amount}[/bold cyan]\nExpecting Code: [italic green]{expected}[/italic green]", title="[blue]Simulation Info[/blue]"))
    else:
        console.print(f"[yellow]Note:[/yellow] No known simulator behavior defined for amount = {amount}")

    console.rule(f"[bold green]Starting Test for {currency}[/bold green]")
    public_key = env['public_key']
    private_key = env['private_key']
    account_id = env[f"account_id_cards_{currency.lower()}"]

    console.print("[bold]1. Verifying API Health...[/bold]")
    health = get_with_logging("https://api.test.paysafe.com/paymenthub/v1/monitor", auth_header(public_key))
    console.print(f"[green]Health Status:[/green] {health['status']}")

    console.print("[bold]2. Fetching Payment Methods...[/bold]")
    methods = get_with_logging(f"https://api.test.paysafe.com/paymenthub/v1/paymentmethods?currencyCode={currency}", auth_header(public_key))
    display_payment_methods(methods.get("paymentMethods", []))

    merchant_ref = generate_merchant_ref()
    console.print(f"[bold]3. Creating Payment Handle... {merchant_ref}[/bold]")
    payload = {
        "merchantRefNum": merchant_ref,
        "transactionType": "PAYMENT",
        "amount": amount,
        "accountId": account_id,
        "paymentType": "CARD",
        "currencyCode": currency,
        "customerIp": "172.0.0.1",
        "returnLinks": [
            {"rel": "on_completed", "href": "https://www.example.com/completed/", "method": "GET"},
            {"rel": "on_failed", "href": "https://www.example.com/failed/", "method": "GET"},
            {"rel": "default", "href": "https://www.example.com/failed/", "method": "GET"}
        ]
    }
    if interactive_flag:
        payload = enrich_payload(payload)
    else:
        # fallback for automation (static test values)
        payload.update({
            "card": {
                "cardNum": "4000000000002503",
                "cardExpiry": {"month": "02", "year": "2026"},
                "cvv": 111,
                "holderName": "John Doe"
            },
            "profile": {
                "firstName": "John",
                "lastName": "Doe",
                "email": "john.doe@paysafe.com"
            },
            "billingDetails": {
                "nickName": "Home",
                "street": "5335 Gate Pkwy",
                "city": "Jacksonville",
                "zip": "32256",
                "country": "US",
                "state": "FL"
            }
        })

    headers = auth_header(private_key)
    headers.update({"Content-Type": "application/json", "Simulator": "INTERNAL"})
    payment_handle = post_with_logging("https://api.test.paysafe.com/paymenthub/v1/paymenthandles", headers, payload)
    handle_token = payment_handle['paymentHandleToken']
    console.print(f"[green]Payment Handle Created:[/green] {handle_token}")

    card_info = f"**** **** **** {payload['card']['cardNum'][-4:]}"
    console.print(Panel.fit(f"About to charge [bold yellow]{amount}[/bold yellow] {currency} using card [cyan]{card_info}[/cyan]", title="[bold blue]Payment Summary[/bold blue]"))

    console.print("[bold]4. Submitting Payment...[/bold]")
    threads = []
    pay_thread = threading.Thread(
        target=submit_payment_and_poll,
        args=(handle_token, merchant_ref, amount, currency, private_key, refund_flag),
        daemon=True
    )
    threads.append(pay_thread)
    pay_thread.start()

    if cancel_flag and 90 <= amount < 100:
        cancel_thread = threading.Thread(
            target=cancel_payment_if_needed_threadsafe,
            args=(private_key,),
            daemon=True
        )
        threads.append(cancel_thread)
        cancel_thread.start()

    for t in threads:
        t.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paysafe Card Payment Test Tool")
    parser.add_argument("--env", help="Path to Postman env JSON", required=True)
    parser.add_argument("--currency", choices=["USD", "GBP"], required=True)
    parser.add_argument("--amount", type=int, help="Amount in minor units (optional if interactive)", required=False)
    parser.add_argument("--refund", action="store_true", help="Trigger refund if payment completes")
    parser.add_argument("--cancel", action="store_true", help="Attempt cancellation if delayed payment")
    parser.add_argument("--interactive", action="store_true", help="Enable interactive form input for card, address, profile, and amount")
    args = parser.parse_args()
    env = load_env(args.env)
    run_test(env, args.currency, args.amount, args.refund, args.cancel, args.interactive)
