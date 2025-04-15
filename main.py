import argparse
import json
import time
import uuid
import requests
import base64
from rich.console import Console
from rich.table import Table
from rich.progress import track

console = Console()

# Load environment

def load_env(env_path):
    with open(env_path, 'r') as f:
        env_data = json.load(f)
        return {item['key']: item['value'] for item in env_data['values'] if item['enabled']}

# Generate UUID-based merchant ref num

def generate_merchant_ref():
    return str(uuid.uuid4())

# Auth header builder

def auth_header(key):
    return {
        "Authorization": f"Basic {key}",
        "Accept": "application/json"
    }

# POST with error handler

def post_with_logging(url, headers, payload):
    response = requests.post(url, headers=headers, json=payload)
    try:
        response.raise_for_status()
        return response.json()
    except Exception as e:
        console.log(f"[bold red]POST Failed:[/bold red] {e}")
        console.log(response.text)
        raise

# GET with error handler

def get_with_logging(url, headers):
    response = requests.get(url, headers=headers)
    try:
        response.raise_for_status()
        return response.json()
    except Exception as e:
        console.log(f"[bold red]GET Failed:[/bold red] {e}")
        console.log(response.text)
        raise

# Main test runner

def run_test(env, currency):
    console.rule(f"[bold green]Starting Test for {currency}[/bold green]")
    public_key = env['public_key']
    private_key = env['private_key']
    account_id = env[f"account_id_cards_{currency.lower()}"]

    # Step 1 - Monitor
    console.print("[bold]1. Verifying API Health...[/bold]")
    health = get_with_logging(
        "https://api.test.paysafe.com/paymenthub/v1/monitor",
        auth_header(public_key)
    )
    console.print(f"[green]Health Status:[/green] {health['status']}")

    # Step 2 - Payment Methods
    console.print("[bold]2. Fetching Payment Methods...[/bold]")
    methods = get_with_logging(
        f"https://api.test.paysafe.com/paymenthub/v1/paymentmethods?currencyCode={currency}",
        auth_header(public_key)
    )
    table = Table(title="Available Payment Methods")
    table.add_column("Method", justify="left")
    for method in methods.get('paymentMethods', []):
        table.add_row(method['paymentMethod'])
    console.print(table)

    # Step 3 - Create Payment Handle
    console.print("[bold]3. Creating Payment Handle...[/bold]")
    merchant_ref = generate_merchant_ref()
    payload = {
        "merchantRefNum": merchant_ref,
        "transactionType": "PAYMENT",
        "amount": 4,
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
    payment_handle = post_with_logging(
        "https://api.test.paysafe.com/paymenthub/v1/paymenthandles",
        headers,
        payload
    )
    handle_token = payment_handle['paymentHandleToken']
    console.print(f"[green]Payment Handle Created:[/green] {handle_token}")

    # Step 4 - Process Payment
    console.print("[bold]4. Submitting Payment...[/bold]")
    payment_payload = {
        "merchantRefNum": merchant_ref,
        "amount": 4,
        "currencyCode": currency,
        "paymentHandleToken": handle_token
    }
    payment = post_with_logging(
        "https://api.test.paysafe.com/paymenthub/v1/payments",
        auth_header(private_key),
        payment_payload
    )
    payment_id = payment['id']
    console.print(f"[green]Payment Submitted. ID:[/green] {payment_id}")

    # Step 5 - Poll for completion
    console.print("[bold]5. Polling for Payment Completion...[/bold]")
    for _ in track(range(10), description="Checking payment status..."):
        status = get_with_logging(
            f"https://api.test.paysafe.com/paymenthub/v1/payments/{payment_id}",
            auth_header(private_key)
        )
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
    args = parser.parse_args()

    env = load_env(args.env)
    run_test(env, args.currency)
