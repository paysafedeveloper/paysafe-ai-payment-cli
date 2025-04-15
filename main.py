import argparse
import json
import time
import uuid
import requests
import base64
import tempfile
import traceback
from rich.console import Console
from rich.table import Table
from rich.progress import track
from rich.panel import Panel

console = Console()

# Simulated response expectation mapping
SIMULATED_RESPONSES = {
    1:  "Approved",
    4:  "The bank has requested that you process the transaction manually by calling the card holder's credit card company.",
    5:  "Your request has been declined by the issuing bank.",
    6:  "Clearing house timeout (although the simulator returns immediately; if delay is desired, see amount 96).",
    11: "The card has been declined due to insufficient funds.",
    12: "Your request has been declined by the issuing bank due to its proprietary card activity regulations.",
    13: "Your request has been declined because the issuing bank does not permit the transaction for this card.",
    20: "An internal error occurred.",
    23: "The transaction was declined by our Risk Management department.",
    24: "Your request has failed the AVS check.",
    25: "The card number or email address associated with this transaction is in our negative database.",
    77: "Your request has been declined because Strong Customer Authentication is required.",
    90: "Approved with 5-second delay",
    91: "Approved with 10-second delay",
    92: "Approved with 15-second delay",
    93: "Approved with 20-second delay",
    94: "Approved with 25-second delay",
    95: "Approved with 30-second delay",
    96: "Declined with 35-second delay. Transaction timed out after 30 seconds.",
    100: "Approved"
}


def load_env(env_path):
    with open(env_path, 'r') as f:
        env_data = json.load(f)
        return {item['key']: item['value'] for item in env_data['values'] if item['enabled']}


def generate_merchant_ref():
    return str(uuid.uuid4())


def auth_header(key):
    return {
        "Authorization": f"Basic {key}",
        "Accept": "application/json"
    }


def post_with_logging(url, headers, payload):
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        try:
            err_json = response.json()
            code = err_json.get("error", {}).get("code", "N/A")
            message = err_json.get("error", {}).get("message", "")
            additional = err_json.get("error", {}).get("additionalDetails", [])
            details = "\n".join([f"[bold cyan]{d.get('type')}[/bold cyan] ({d.get('code')}): {d.get('message')}" for d in additional])
            summary = f"[red]Error Code:[/red] {code}\n[red]Message:[/red] {message}\n{details}"
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


def run_test(env, currency, amount):
    expected = SIMULATED_RESPONSES.get(amount)
    if expected:
        console.print(Panel.fit(f"Simulated Amount: [bold cyan]{amount}[/bold cyan]\nExpecting: [italic green]{expected}[/italic green]", title="[blue]Simulation Info[/blue]"))
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
    table = Table(title="Available Payment Methods")
    table.add_column("Method")
    table.add_column("Usage")
    table.add_column("Category")
    for method in methods.get('paymentMethods', []):
        table.add_row(
            method.get('paymentMethod', 'N/A'),
            method.get('usage', 'N/A'),
            method.get('paymentTypeCategory', 'N/A')
        )
    console.print(table)

    console.print("[bold]3. Creating Payment Handle...[/bold]")
    merchant_ref = generate_merchant_ref()
    payload = {
        "merchantRefNum": merchant_ref,
        "transactionType": "PAYMENT",
        "amount": amount,
        "accountId": account_id,
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
        "paymentType": "CARD",
        "currencyCode": currency,
        "customerIp": "172.0.0.1",
        "billingDetails": {
            "nickName": "Home",
            "street": "5335 Gate Pkwy",
            "city": "Jacksonville",
            "zip": "32256",
            "country": "US",
            "state": "FL"
        },
        "returnLinks": [
            {"rel": "on_completed", "href": "https://www.example.com/completed/", "method": "GET"},
            {"rel": "on_failed", "href": "https://www.example.com/failed/", "method": "GET"},
            {"rel": "default", "href": "https://www.example.com/failed/", "method": "GET"}
        ]
    }
    headers = auth_header(private_key)
    headers.update({"Content-Type": "application/json", "Simulator": "INTERNAL"})
    payment_handle = post_with_logging("https://api.test.paysafe.com/paymenthub/v1/paymenthandles", headers, payload)
    handle_token = payment_handle['paymentHandleToken']
    console.print(f"[green]Payment Handle Created:[/green] {handle_token}")

    card_info = f"**** **** **** {payload['card']['cardNum'][-4:]}"
    console.print(Panel.fit(f"About to charge [bold yellow]{amount}[/bold yellow] {currency} using card [cyan]{card_info}[/cyan]", title="[bold blue]Payment Summary[/bold blue]"))

    console.print("[bold]4. Submitting Payment...[/bold]")
    payment_payload = {
        "merchantRefNum": merchant_ref,
        "amount": amount,
        "currencyCode": currency,
        "paymentHandleToken": handle_token
    }
    payment = post_with_logging("https://api.test.paysafe.com/paymenthub/v1/payments", auth_header(private_key), payment_payload)
    payment_id = payment['id']
    console.print(f"[green]Payment Submitted. ID:[/green] {payment_id}")

    console.print("[bold]5. Polling for Payment Completion...[/bold]")
    for _ in track(range(10), description="Checking payment status..."):
        status = get_with_logging(f"https://api.test.paysafe.com/paymenthub/v1/payments/{payment_id}", auth_header(private_key))
        if status['status'] == 'COMPLETED':
            console.print(f"[bold green]Payment Completed[/bold green] ✅")
            break
        time.sleep(2)
    else:
        console.print(f"[bold red]Payment not completed in time.[/bold red]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paysafe Card Payment Test Tool")
    parser.add_argument("--env", help="Path to Postman env JSON", required=True)
    parser.add_argument("--currency", choices=["USD", "GBP"], required=True)
    parser.add_argument("--amount", type=int, help="Amount in minor units", required=True)
    args = parser.parse_args()
    env = load_env(args.env)
    run_test(env, args.currency, args.amount)
