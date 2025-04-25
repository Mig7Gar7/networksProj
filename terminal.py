import os
import sys
import time
import json
import sqlite3
import logging
import requests
import datetime
import socket
import board
import busio
from digitalio import DigitalInOut
from adafruit_pn532.i2c import PN532_I2C

# logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("terminal.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('terminal')

# config
SERVER_URL = "http://your-server-address:port"  # replace with IP and port
TERMINAL_ID = socket.gethostname()
DB_PATH = "terminal.db"
BALANCE_FILE = "card_balances.json"
DEFAULT_BALANCE = 50.00
FARE_AMOUNT = 2.50

# le database
def get_db_connection():
    return sqlite3.connect(DB_PATH)

def init_database():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0.0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            amount REAL NOT NULL,
            balance_before REAL,
            balance_after REAL,
            transaction_type TEXT,
            terminal_id TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            synced INTEGER DEFAULT 0
        )''')
        conn.commit()
        conn.close()
        logger.info("Database initialized")
        return True
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return False

# le reader
def init_nfc_reader():
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        reset_pin = DigitalInOut(board.D6)
        req_pin = DigitalInOut(board.D12)
        pn532 = PN532_I2C(i2c, debug=False, reset=reset_pin, req=req_pin)
        ic, ver, rev, support = pn532.firmware_version
        logger.info(f"PN532 firmware version: {ver}.{rev}")
        pn532.SAM_configuration()
        return pn532
    except Exception as e:
        logger.error(f"NFC init error: {e}")
        raise

def read_card_uid(pn532):
    logger.info("Waiting for card...")
    while True:
        try:
            uid = pn532.read_passive_target(timeout=0.5)
            if uid is None:
                time.sleep(0.1)
                continue
            uid_hex = ''.join([f'{i:02X}' for i in uid])
            logger.info(f"Card UID: {uid_hex}")
            return uid_hex
        except Exception as e:
            logger.debug(f"NFC read error: {e}")
            time.sleep(0.5)

# server
def check_server_connection():
    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=2)
        return response.status_code == 200
    except Exception as e:
        logger.warning(f"Server not reachable: {e}")
        return False

def sync_transactions():
    if not check_server_connection():
        logger.warning("Server unavailable, skipping sync")
        return

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, account_id, amount, balance_before, balance_after, transaction_type, terminal_id, timestamp FROM transactions WHERE synced = 0")
        transactions = cursor.fetchall()

        if not transactions:
            logger.info("No transactions to sync")
            return

        logger.info(f"Syncing {len(transactions)} transactions")

        for tx in transactions:
            tx_id, account_id, amount, before, after, tx_type, term_id, timestamp = tx
            payload = {
                "terminal_id": term_id or TERMINAL_ID,
                "uid": account_id,
                "amount": amount,
                "balance_before": before,
                "balance_after": after,
                "transaction_type": tx_type,
                "timestamp": timestamp
            }

            try:
                response = requests.post(f"{SERVER_URL}/sync_transaction", json=payload)
                if response.status_code == 200:
                    cursor.execute("UPDATE transactions SET synced = 1 WHERE id = ?", (tx_id,))
                    conn.commit()
                    logger.info(f"Transaction {tx_id} synced")
                else:
                    logger.warning(f"Sync failed for {tx_id}: {response.status_code}")
            except Exception as e:
                logger.error(f"Sync error for {tx_id}: {e}")

    except Exception as e:
        logger.error(f"Sync process error: {e}")
    finally:
        conn.close()

# balances
def get_card_balances():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Balance file error: {e}")
    return {}

def save_card_balances(balances):
    try:
        with open(BALANCE_FILE, 'w') as f:
            json.dump(balances, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving balances: {e}")
        return False

def get_card_balance(card_id):
    if check_server_connection():
        try:
            response = requests.get(f"{SERVER_URL}/get_card_balance/{card_id}", params={"terminal_id": TERMINAL_ID})
            if response.status_code == 200:
                balance = response.json()["balance"]
                balances = get_card_balances()
                balances[card_id] = balance
                save_card_balances(balances)
                return balance
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")

    balances = get_card_balances()
    if card_id in balances:
        return balances[card_id]
    else:
        balances[card_id] = DEFAULT_BALANCE
        save_card_balances(balances)
        logger.info(f"Assigned default balance for new card {card_id}")
        return DEFAULT_BALANCE

def update_card_balance(card_id, new_balance):
    balances = get_card_balances()
    balances[card_id] = new_balance
    if save_card_balances(balances):
        logger.info(f"Updated balance for {card_id}: ${new_balance}")
        return True
    return False

# transactions
def register_transaction(card_id, amount, before, after):
    timestamp = datetime.datetime.now().isoformat()
    tx_type = "payment" if amount < 0 else "topup"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""INSERT INTO transactions (
            account_id, amount, balance_before, balance_after,
            transaction_type, terminal_id, timestamp, synced
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (card_id, amount, before, after, tx_type, TERMINAL_ID, timestamp, 0))
        conn.commit()
        logger.info(f"Transaction recorded: {card_id}, ${amount}")
        return True
    except Exception as e:
        logger.error(f"Register transaction error: {e}")
        return False
    finally:
        conn.close()

def process_fare_payment(card_id):
    current_balance = get_card_balance(card_id)
    if current_balance < FARE_AMOUNT:
        print("Insufficient funds")
        return False

    new_balance = current_balance - FARE_AMOUNT
    if check_server_connection():
        try:
            payload = {
                "uid": card_id,
                "fare": FARE_AMOUNT,
                "terminal_id": TERMINAL_ID
            }
            response = requests.post(f"{SERVER_URL}/process_payment", json=payload)
            if response.status_code == 200:
                new_balance = response.json()["new_balance"]
        except Exception as e:
            logger.error(f"Payment server error: {e}")
    if update_card_balance(card_id, new_balance):
        register_transaction(card_id, -FARE_AMOUNT, current_balance, new_balance)
        return True
    return False

def process_topup(card_id, amount):
    if amount <= 0:
        print("Topup must be positive")
        return False

    current_balance = get_card_balance(card_id)
    new_balance = current_balance + amount

    if check_server_connection():
        try:
            payload = {
                "uid": card_id,
                "amount": amount,
                "terminal_id": TERMINAL_ID
            }
            response = requests.post(f"{SERVER_URL}/topup_card", json=payload)
            if response.status_code == 200:
                new_balance = response.json()["new_balance"]
        except Exception as e:
            logger.error(f"Topup server error: {e}")

    if update_card_balance(card_id, new_balance):
        register_transaction(card_id, amount, current_balance, new_balance)
        return True
    return False

# card wait
def wait_for_card_removal(pn532, seconds=3):
    print("\nRemove card...")
    start = time.time()
    while time.time() - start < seconds:
        if pn532.read_passive_target(timeout=0.1) is None:
            print("Card removed")
            break
        time.sleep(0.2)
    time.sleep(max(0, seconds - (time.time() - start)))
    print("===================================")

def main():
    if not init_database():
        logger.error("Database init failed")
        return

    try:
        pn532 = init_nfc_reader()
    except Exception as e:
        logger.error(f"NFC init failed: {e}")
        return

    sync_transactions()
    print(f"\n===== Terminal Ready =====\nTap card to pay ${FARE_AMOUNT:.2f}")

    try:
        while True:
            try:
                card_id = read_card_uid(pn532)
                if not card_id:
                    continue
                print(f"\nCard: {card_id}")
                process_fare_payment(card_id)
                wait_for_card_removal(pn532, 3)
                if datetime.datetime.now().second % 30 < 5:
                    sync_transactions()
            except Exception as e:
                logger.error(f"Card process error: {e}")
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        sync_transactions()

if __name__ == "__main__":
    main()
