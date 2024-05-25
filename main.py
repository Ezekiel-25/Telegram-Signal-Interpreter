import os
import threading
import re
import requests

from collections import Counter
from functools import wraps
from pathlib import Path
from time import sleep

from binance.enums import *
from binance.exceptions import BinanceAPIException
from binance.client import Client as BinClient

from pyrogram import filters, Client as PyClient
from pyrogram.handlers import MessageHandler

from dotenv import load_dotenv


# -----------------------------Global Variables Initialization------------------
def load_environment_variables():
    """Load environment variables from the .env file."""
    try:
        script_dir = Path(__file__).resolve().parent
    except NameError:
        script_dir = Path(os.getcwd())
    env_file_path = script_dir / ".env"
    load_dotenv(env_file_path)


load_environment_variables()

BIN_KEY = os.getenv("API_Key")
BIN_SECRET = os.getenv("API_SECRET")
TEL_ID = os.getenv("id")
TEL_HASH = os.getenv("hash")
CHAT_ID = os.getenv("Chat_id")
TOKEN = os.getenv("Bot_Token")
# ---------------------------END OF Global Variables Initialization-------------

# ----------------------------Binance and Telegram Clients Initialization--------
# Binance Client
Bin = BinClient(BIN_KEY, BIN_SECRET)
# Pyrogram Client
Py = PyClient("Aagreb", api_id=TEL_ID, api_hash=TEL_HASH)


# ------------------------END OF Binance and Telegram Clients Initialization-----

# Relay Function
def relay(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}&text={text}"
    requests.get(url).json()


# ----------------------Extraction Function---------------------------
def extraction(signal: str) -> dict:
    DicValues = {}
    pop = 0
    i = 0
    coin_symbol = ''
    if signal.count('.') < 3:
        signal = signal.replace('.', '')
    while signal[i] not in ['/', ' ', 'U']:
        coin_symbol += signal[i]
        i = i + 1
    DicValues.update({"Symbol": re.sub(r'[^A-Z]', '', coin_symbol) + "USDT"})
    match = re.search(r'(LONG|SHORT)', signal)
    type = match.group(1)
    if type == 'LONG':
        DicValues.update({"Side": SIDE_BUY})
        DicValues.update({"OppSide": SIDE_SELL})
    elif type == 'SHORT':
        DicValues.update({"Side": SIDE_SELL})
        DicValues.update({"OppSide": SIDE_BUY})
    signal = re.sub(r'_', '\n', signal)
    signal = re.sub(r'[^0-9.\n]', '', signal)
    signal = os.linesep.join([s for s in signal.splitlines() if s])
    Values = [(number.strip()) for number in signal.split('\n') if number.strip()]
    while Values[0].isdigit() and pop < 2:
        Values.pop(0)
        pop += 1
    try:
        DicValues.update({"Entry": [float(value) for value in Values[0:2]]})
    except ValueError:
        Values[0] = Values[0][1:]
        DicValues.update({"Entry": [float(value) for value in Values[0:2]]})
    if type == 'LONG':
        DicValues["Entry"].sort()
    elif type == 'SHORT':
        DicValues["Entry"].sort(reverse=True)
    DicValues.update({"Targets": [float(value) for value in Values[2:-1]]})
    DicValues.update({"SL": float(Values[-1])})
    DicValues.update({"TP": float(Values[-2])})
    DicValues.update({"AP": float(Values[2])})
    return DicValues


# ----------------END OF Extraction Function--------------------------

# ---------------------Decorators-------------------------------------
def bin_tele_relay(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except BinanceAPIException as err:
            error_msg = f"{func.__name__} raised a BinanceEx, Reason: {err}"
            relay(error_msg)
        except KeyError as err:
            error_msg = f"{func.__name__} encountered a KeyError, Reason: {err}"
            relay(error_msg)

    return wrapper


# -------------------END OF Decorators--------------------------------


# ------------------API CALL METHODS--------------------------------------------
@bin_tele_relay
def Get_Balance() -> float:
    account_info = Bin.futures_account()
    for balance in account_info['assets']:
        if balance['asset'] == 'USDT':
            return float(balance['walletBalance'])


@bin_tele_relay
def Get_markPrice(symbol: str) -> float:
    return float(Bin.futures_symbol_ticker(symbol=symbol)['price'])


@bin_tele_relay
def Set_Lev(symbol: str) -> int:
    lev = int(Bin.futures_leverage_bracket(symbol=symbol)[0]['brackets'][0]['initialLeverage'])
    Bin.futures_change_leverage(symbol=symbol, leverage=lev)
    return lev


# ------------------ END OF API CALL METHODS-------------------------------------

# -----------------------------Calculation Methods----------------------------------------
def det_callback(targets: list) -> float:
    differences = []
    for i in range(len(targets) - 1):
        percentage_diff = abs((targets[i + 1] - targets[i]) / targets[i]) * 100
        differences.append(percentage_diff)
    if differences:
        return round(sum(differences) / len(differences), 2)
    else:
        return 0


def det_entery(markPrice: float, EntryList: list) -> float:
    EntryList[0], EntryList[1] = min(EntryList[0], EntryList[1]), max(EntryList[0], EntryList[1])
    if EntryList[0] <= markPrice <= EntryList[1]:
        return ORDER_TYPE_MARKET
    else:
        if abs(markPrice - EntryList[0]) < abs(markPrice - EntryList[1]):
            return EntryList[0]
        else:
            return EntryList[1]


# -----------------------------END OF Calculation Methods---------------------------------

# ---------------------------------Order Handeling Functions-------------------------------
@bin_tele_relay
def cancel_open_orders(symbol: str, orderlist: list):
    for order in orderlist:
        Bin.futures_cancel_order(symbol=symbol, orderId=order)


@bin_tele_relay
def order_aufpassen(symbol: str, orderList: list):
    origlist = [order["type"] for order in Bin.futures_get_open_orders(symbol=symbol) if "MARKET" in order["type"]]
    while True:
        sleep(90)
        updlist = [order["type"] for order in Bin.futures_get_open_orders(symbol=symbol) if "MARKET" in order["type"]]

        if len(updlist) != len(origlist):
            cancel_open_orders(orderList)
            count1 = Counter(origlist)
            count2 = Counter(updlist)
            for key in count1:
                if count1[key] != count2[key]:
                    relay(f"{key} Order was Filled, Position Should be closed")
                    return 1


def place_SL_order(symbol: str, side: str, stopPrice: float):
    order = Bin.futures_create_order(symbol=symbol, side=side,
                                     type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=stopPrice,
                                     timeInForce="GTC", priceProtect='true', workingType='MARK_PRICE',
                                     closePosition='true')
    return order["orderID"]


def place_TP_order(symbol: str, side: str, stopPrice: str):
    order = Bin.futures_create_order(symbol=symbol, side=side,
                                     type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, stopPrice=stopPrice,
                                     timeInForce="GTC", priceProtect='true', workingType='MARK_PRICE',
                                     closePosition='true')
    return order["orderID"]


def place_initial_limit_order(symbol: str, qnt: str, side: str, entry: float):
    order = Bin.futures_create_order(symbol=symbol, quantity=qnt, side=side,
                                     type=FUTURE_ORDER_TYPE_LIMIT, price=entry, timeInForce="GTC")
    return order["orderID"]


def place_initial_market_order(symbol: str, qnt: str, side: str):
    order = Bin.futures_create_order(symbol=symbol, quantity=qnt, side=side,
                                     type=FUTURE_ORDER_TYPE_MARKET)
    return order["orderID"]


def place_trailing_SL_order(symbol: str, qnt: str, side: str, ap: str, callback: float):
    order = Bin.futures_create_order(symbol=symbol, quantity=qnt, side=side,
                                     type=FUTURE_ORDER_TYPE_TRAILING_STOP_MARKET,
                                     activationPrice=ap, callbackRate=callback, workingType='MARK_PRICE',
                                     timeInForce="GTC")
    return order["orderID"]


def place_order(DicValues: dict):
    balance = Get_Balance()
    markPrice = Get_markPrice(DicValues["Symbol"])
    leverage = Set_Lev(DicValues["Symbol"])
    if balance is None or markPrice is None or leverage is None:
        return 0
    entry = det_entery(markPrice, DicValues["Entry"])
    quantity = round((balance * 0.03) / markPrice, 3)
    orderList = []
    try:
        orderList.append(
            place_SL_order(symbol=DicValues["Symbol"], side=DicValues["OppSide"], stopPrice=round(DicValues["SL"])))
        relay(f"SL Order Placed stopPrice = {round(DicValues['SL'])}")
        orderList.append(
            place_TP_order(symbol=DicValues["Symbol"], side=DicValues["OppSide"], stopPrice=str(DicValues["TP"])))
        relay(f"TP Order Placed stopPrice = {DicValues['TP']}")
        if entry == ORDER_TYPE_MARKET:
            orderList.append(
                place_initial_market_order(symbol=DicValues["Symbol"], qnt=str(quantity), side=DicValues["Side"]))
            relay(f"Market {DicValues['Side']} Order Placed qnty = {quantity}")
        else:
            orderList.append(
                place_initial_limit_order(symbol=DicValues["Symbol"], qnt=str(quantity), side=DicValues["Side"],
                                          entry=entry))
            relay(f"Market {DicValues['Side']} Order Placed qnty = {quantity}, entry= {entry}")
    except BinanceAPIException as err:
        cancel_open_orders(symbol=DicValues["Symbol"], orderlist=orderList)
        error_msg = f"In the chain SL->TP->Initial a BinanceException Reason: {err}"
        relay(error_msg)
        return 0
    callback = det_callback(DicValues["Targets"])
    quantity = round((balance * 0.031) / markPrice,
                     3)  # Slightly a bigger quantity to account for the difference due to Bianance calculations
    try:
        orderList.append(
            place_trailing_SL_order(symbol=DicValues["Symbol"], qnt=str(quantity), side=DicValues["OppSide"],
                                    ap=str(DicValues["AP"]), callback=callback))
        relay(f"Trailing SL Order Placed AP = {DicValues['AP']}")
    except BinanceAPIException as err:
        relay(f"Trailing SL Order Failed with AP = {DicValues['AP']}, Reason: {err}")
    order_aufpassen(symbol=DicValues["Symbol"], orderList=orderList)
    relay(f"Yaw Nik 3omri! {DicValues['Symbol']} Order Complete")


# ---------------------------------END OF Order Handeling Functions------------------------


# ---------------------------------Pyrogram Listner Funtion--------------------------------
@Py.on_message(filters.channel & (filters.text | filters.photo))
async def process_message(client, message):
    if message.chat.title == [Channel Name]:
        try:
            message_text_upper = message.text.upper()
            if "SHORT" in message_text_upper or "LONG" in message_text_upper:
                relay("Signal Received")
                threading.Thread(target=place_order, args=(extraction(message_text_upper),)).start()
            else:
                await Py.send_message(chat_id=CHAT_ID, text=f"InfoMsg: {message.text}")
        except AttributeError:
            await Py.send_photo(chat_id=CHAT_ID, photo=message.photo.file_id, caption=message.caption)


# ------------------------------END OF Pyrogram Listener Function---------------------------


def main():
    my_handler = MessageHandler(process_message)
    Py.add_handler(my_handler)
    relay("Bot Started")
    Py.run()
    relay("Bot Stopped")


if __name__ == "__main__":
    main()
