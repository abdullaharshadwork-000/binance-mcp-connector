import hashlib
import hmac
import os
import time
from datetime import date
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ["BINANCE_API_KEY"]
API_SECRET = os.environ["BINANCE_API_SECRET"]
BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://testnet.binance.vision")
URL_SECRET = os.environ["MCP_URL_SECRET"]

ENABLE_TRADING = os.environ.get("ENABLE_TRADING", "false").lower() == "true"
MAX_ORDER_USDT = float(os.environ.get("MAX_ORDER_USDT", "20"))
MAX_DAILY_USDT = float(os.environ.get("MAX_DAILY_USDT", "50"))
ALLOWED_SYMBOLS = {
    s.strip().upper()
    for s in os.environ.get("ALLOWED_SYMBOLS", "BTCUSDT,ETHUSDT").split(",")
    if s.strip()
}

PORT = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(
    "binance",
    host="0.0.0.0",
    port=PORT,
    streamable_http_path=f"/{URL_SECRET}/mcp",
)

_daily = {"day": date.today(), "spent": 0.0}


def _sign(params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    return params


def _request(method: str, path: str, params: dict | None = None, signed: bool = False):
    params = params or {}
    headers = {"X-MBX-APIKEY": API_KEY}
    if signed:
        params = _sign(params)
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        resp = client.request(method, path, params=params, headers=headers)
    if resp.status_code >= 400:
        return {"error": resp.status_code, "detail": resp.text}
    return resp.json()


@mcp.tool()
def get_price(symbol: str) -> dict:
    """Get the latest price for a symbol such as BTCUSDT."""
    return _request("GET", "/api/v3/ticker/price", {"symbol": symbol.upper()})


@mcp.tool()
def get_order_book(symbol: str, limit: int = 10) -> dict:
    """Get the top bids and asks for a symbol."""
    return _request(
        "GET", "/api/v3/depth", {"symbol": symbol.upper(), "limit": limit}
    )


@mcp.tool()
def get_candles(symbol: str, interval: str = "1h", limit: int = 100) -> list | dict:
    """Get candlestick data. Interval examples: 1m, 15m, 1h, 4h, 1d."""
    return _request(
        "GET",
        "/api/v3/klines",
        {"symbol": symbol.upper(), "interval": interval, "limit": limit},
    )


@mcp.tool()
def get_balances() -> dict:
    """Get non-zero spot balances for the account."""
    data = _request("GET", "/api/v3/account", signed=True)
    if "balances" not in data:
        return data
    data["balances"] = [
        b
        for b in data["balances"]
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    ]
    return {"balances": data["balances"], "canTrade": data.get("canTrade")}


@mcp.tool()
def get_open_orders(symbol: str | None = None) -> list | dict:
    """List open orders, optionally for one symbol."""
    params = {"symbol": symbol.upper()} if symbol else {}
    return _request("GET", "/api/v3/openOrders", params, signed=True)


@mcp.tool()
def get_trading_limits() -> dict:
    """Show the safety limits currently enforced by this server."""
    return {
        "trading_enabled": ENABLE_TRADING,
        "environment": BASE_URL,
        "max_order_usdt": MAX_ORDER_USDT,
        "max_daily_usdt": MAX_DAILY_USDT,
        "allowed_symbols": sorted(ALLOWED_SYMBOLS),
        "spent_today_usdt": round(_daily["spent"], 2),
    }


@mcp.tool()
def place_order(
    symbol: str,
    side: str,
    quantity: float,
    price: float | None = None,
) -> dict:
    """Place a spot order. Omit price for a market order. Side is BUY or SELL.
    Subject to the enabled flag, allowed symbols, per order cap and daily cap."""
    if not ENABLE_TRADING:
        return {"error": "Trading is disabled. Set ENABLE_TRADING=true to allow orders."}

    symbol = symbol.upper()
    side = side.upper()
    if side not in ("BUY", "SELL"):
        return {"error": "side must be BUY or SELL"}
    if symbol not in ALLOWED_SYMBOLS:
        return {"error": f"{symbol} is not in the allowed symbols list"}
    if quantity <= 0:
        return {"error": "quantity must be positive"}

    ref_price = price
    if ref_price is None:
        ticker = _request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        if "price" not in ticker:
            return ticker
        ref_price = float(ticker["price"])
    notional = quantity * ref_price

    if notional > MAX_ORDER_USDT:
        return {
            "error": f"Order value {notional:.2f} exceeds the per order cap of {MAX_ORDER_USDT}"
        }

    today = date.today()
    if _daily["day"] != today:
        _daily["day"] = today
        _daily["spent"] = 0.0
    if _daily["spent"] + notional > MAX_DAILY_USDT:
        return {
            "error": f"Order would exceed the daily cap of {MAX_DAILY_USDT}. Spent so far: {_daily['spent']:.2f}"
        }

    params = {"symbol": symbol, "side": side, "quantity": quantity}
    if price is None:
        params["type"] = "MARKET"
    else:
        params["type"] = "LIMIT"
        params["price"] = price
        params["timeInForce"] = "GTC"

    result = _request("POST", "/api/v3/order", params, signed=True)
    if "error" not in result:
        _daily["spent"] += notional
    return result


@mcp.tool()
def cancel_order(symbol: str, order_id: int) -> dict:
    """Cancel an open order by its id."""
    if not ENABLE_TRADING:
        return {"error": "Trading is disabled. Set ENABLE_TRADING=true to allow cancels."}
    return _request(
        "DELETE",
        "/api/v3/order",
        {"symbol": symbol.upper(), "orderId": order_id},
        signed=True,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
