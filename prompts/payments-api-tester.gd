# Prompt to Recreate PaysafePilot CLI Tool

Create a Python CLI tool that simulates card payments via Paysafe’s test environment APIs.

## Functional Requirements:

1. Accept:
   - A Postman-style environment file (JSON) with keys like `public_key`, `private_key`, `account_id_cards_usd`, etc.
   - A `currency` flag (`USD` or `GBP`)
   - An `amount` flag in minor units
   - Optional flags:
     - `--refund` to perform refund after payment success
     - `--cancel` to attempt cancelling delayed payments (amounts 90–99)

2. Perform:
   - GET `/paymenthub/v1/monitor` to verify API status
   - GET `/paymentmethods?currencyCode=XXX` to list available payment methods
   - POST `/paymenthandles` to generate a handle with test card
   - POST `/payments` with the handle token
   - Poll GET `/payments/{id}` until `COMPLETED` or timeout

3. Conditional Logic:
   - If `--cancel` is used and amount in range 90–99, issue a `PUT` to `/payments/{id}` with `status: CANCELLED`
   - If `--refund` is used and a `settlementId` is returned, send `POST /settlements/{id}/refunds` and poll `/refunds/{id}`

4. Enrich with:
   - Output using the `rich` package (panels, tables, progress)
   - Expected response checking from `expect_response.json` (match `error_code`)
   - Threading to allow cancellation independently of payment flow
   - Traceback handling: write exceptions to temp files and print `cat` command

5. Examples:

```bash
python main.py --env secrets/paysafe.json --currency USD --amount 4
python main.py --env secrets/paysafe.json --currency USD --amount 91 --cancel
python main.py --env secrets/paysafe.json --currency USD --amount 400 --refund
```

## Bonus

- Use test card `4000000000002503` with dummy address and email
- Use `merchantRefNum` as a UUID
- Poll refunds up to 10 times with delay
- Don’t block refund/cancel in main thread
