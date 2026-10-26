# Receive from a document: reading an invoice

The Receive window (Inventory Items > Receive) has a **Fill from a document** box. Choose the supplier's invoice or delivery document (PDF, JPG or PNG, up to 10 MB), press **Read document**,
and the window proposes values. **Nothing is saved until you press Receive.**

## What you see

- Every value taken from the document is tagged **from document**. The tag goes as soon as you change that field, so what is still tagged is what you have not looked at.
- A value the document does not show is **left blank**. KelpWorks never fills a gap from a default, from an earlier delivery or from a guess (an ambiguous date such as 5/6/2026 is left blank).
- A value that cannot be placed is shown under the box as "Document says: ...": a supplier that is not on this item's approved list, a quantity in boxes when the item is counted in kg, one quantity that covers two lot numbers.
- If the document lists several products, the **Document line for this item** picker appears. A line is chosen for you only when its supplier code was received for this item before, or its description contains the
  item's name; otherwise you choose. Choosing a line fills the lot rows (one row per lot number on the line).
- **Warnings** (red) mean the numbers read do not add up (quantity x unit price against the line amount, or the lines + freight + tax against the invoice total). Nothing is changed or blocked: check the document.
- The document stays attached to the delivery as an invoice.
- Fields the Receive window did not have before are now on the delivery: **currency, freight, tax, invoice total**; each lot also keeps the **description and amount as printed on the invoice** (the delivery window shows them).

## What can be read

| Document | Read how | Needs |
|---|---|---|
| PDF with a text layer (as emailed by many suppliers) | On the server, no outside service | Nothing |
| Scanned PDF, photo (JPG, PNG) | By the Claude service from the page image | An administrator switches it on (below) |
| Anything else, or reading is off | Not read: the file is attached and the fields stay blank | |

The text reader recognises the usual labels (English and French): invoice number and date, PO, order number, bill of lading / PRO, ship date, currency, subtotal, freight, GST / HST, total, and product lines
whose quantity x unit price = amount. It leaves out anything it is not sure of. Check what it filled.

## Switching on the reading of scans and photos (administrators)

Off by default. When it is on, **the page image is sent to the Anthropic (Claude) service**; an invoice shows prices, account numbers and sometimes bank details, so decide first whether that is acceptable. Each read costs a few cents.

1. Set the environment variable **`KELP_ERP_ANTHROPIC_KEY`** on the server (Render > Environment). The key is never stored in the database and never written to the log. Try it on **staging first**.
2. On the Calculations page, group **Receiving: reading invoices**, set **Receive: read scans and photos of invoices with the Claude service** to 1.
3. **Receive: most scans / photos read per day** (default 30) stops a runaway cost: once it is used up, Receive says so and the values are typed by hand.

Every read is logged (who, when, file name and checksum, which reader, what was proposed) in `document_reads`, and the delivery it was used for is linked to it (History of the delivery: "Filled from document").
If the service is unreachable, slow or refuses, Receive says so and works as it always did.

## Checking how well it reads your invoices

`tests/invoice_expectations.json` lists what each of the 8 test invoices shows. On your own computer, with your own key:

```
set KELP_ERP_ANTHROPIC_KEY=<your key>
python tools/check_invoice_reading.py "<folder with the invoice PDFs>"
```

It prints one line for every field that is wrong or missing and a total (add `--only Univar` to read one invoice). Run it on staging-side data only, and after any change to the prompt or the schema.
