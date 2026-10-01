# Creative Spirit shop

This project is a small full-stack shop built with the existing HTML/CSS pages, a Python standard-library HTTP server, and SQLite. There are no third-party Python packages to install.

## How it works

- The gallery is the product catalog. Product data is read from the cards in `Gallery.html`, so the order API only accepts paintings listed there.
- The browser cart keeps its selected items on that device so it survives a page refresh.
- Checkout sends customer details and item quantities to `POST /api/orders`.
- `server.py` validates the request and stores it in `orders.sqlite3`; an order number is returned only after the database write succeeds.
- `Orders.html` reads orders through an admin-token-protected endpoint and can update their status. It also shows messages sent from `Contact.html`.
- The contact page stores custom-art inquiries on the server in the same SQLite database.

## Run it locally

Open PowerShell in this folder and run:

```powershell
python server.py
```

Open `http://127.0.0.1:8000/` for the shop. Use `http://127.0.0.1:8000/Gallery.html` for the catalog, `http://127.0.0.1:8000/Contact.html` for inquiries, and `http://127.0.0.1:8000/Orders.html` for the private order dashboard.

Do not open the HTML files directly using `file://`: the checkout and contact forms need the Python server. The server prints an admin token at startup. Enter that token in the dashboard; keep it private. For a stable admin token, set `CREATIVE_SPIRIT_ADMIN_TOKEN` in the server environment before starting it.

## Before taking public orders

The application currently records order requests and custom-art inquiries, but it does not charge customers, send email/WhatsApp alerts, or calculate prices. Artwork pricing is left as ?price on request? because no prices were supplied. Add your prices and select a payment method before advertising fixed-price checkout.

To publish it, deploy the Python service behind HTTPS on a host that supports a persistent disk, set `CREATIVE_SPIRIT_ADMIN_TOKEN` to a strong secret, and set `CREATIVE_SPIRIT_DB` to a path on that persistent disk. SQLite data stored on an ordinary temporary deployment filesystem can be lost when the service restarts. The repository includes a `Procfile`; the app listens on the host's `PORT` and binds publicly when `PORT` is provided. Attach your domain and enable HTTPS in the hosting provider.

This repository does not include a hosting account, domain, payment-provider credentials, or an external notification address, so it is prepared to deploy but not published to the internet yet.
