import os
import sys
import json
import time
import uuid
import base64
import hashlib
import logging
import sqlite3
import threading
import datetime
import requests
import urllib3
from binascii import hexlify
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# disable ssl warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
print("NOTE: SSL certificate verification is disabled for development.")
print("In production, proper certificates should be used.")

# config settings
ENCRYPTION_KEY_FILE = "terminal_key.key"
SALT = b'bus_terminal_salt_value_123'  # change for prod
SERVER_URL = "https://SERVER_IP:8443"  # use server ip
TERMINAL_ID = str(uuid.uuid4())[:8]
FARE_AMOUNT = 2.50
DEFAULT_BALANCE = 50.00
DB_FILE = "terminal.db"
BALANCE_FILE = "balances.json"
server_available = False

# logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("terminal.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('terminal')

# encryption setup
def generate_encryption_key():
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as key_file:
            return key_file.read()
    else:
        password = f"{TERMINAL_ID}_secure_password".encode()  # change for prod
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=SALT,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        with open(ENCRYPTION_KEY_FILE, 'wb') as key_file:
            key_file.write(key)
        return key

try:
    encryption_key = generate_encryption_key()
    cipher = Fernet(encryption_key)
    ENCRYPTION_ENABLED = True
    print("Data encryption enabled")
except Exception as e:
    print(f"WARNING: Encryption initialization failed: {e}")
    print("Data will be stored unencrypted")
    ENCRYPTION_ENABLED = False

# encryption functions
def encrypt_data(data):
    if not ENCRYPTION_ENABLED:
        return data
    if isinstance(data, (int, float)):
        data = str(data)
    if isinstance(data, str):
        return cipher.encrypt(data.encode()).decode()
    return data

def decrypt_data(data):
    if not ENCRYPTION_ENABLED or data is None:
        return data
    if isinstance(data, str):
        try:
            return cipher.decrypt(data.encode()).decode()
        except Exception:
            return data
    return data

def encrypt_json(data):
    if not ENCRYPTION_ENABLED:
        return data
    encrypted_data = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float)):
            encrypted_data[key] = encrypt_data(str(value))
        elif isinstance(value, dict):
            encrypted_data[key] = encrypt_json(value)
        else:
            encrypted_data[key] = value
    return encrypted_data

def decrypt_json(data):
    if not ENCRYPTION_ENABLED:
        return data
    decrypted_data = {}
    for key, value in data.items():
        if isinstance(value, str):
            try:
                decrypted_value = decrypt_data(value)
                if decrypted_value.replace('.', '', 1).isdigit():
                    if '.' in decrypted_value:
                        decrypted_data[key] = float(decrypted_value)
                    else:
                        decrypted_data[key] = int(decrypted_value)
                else:
                    decrypted_data[key] = decrypted_value
            except Exception:
                decrypted_data[key] = value
        elif isinstance(value, dict):
            decrypted_data[key] = decrypt_json(value)
        else:
            decrypted_data[key] = value
    return decrypted_data

# database functions
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                balance_before TEXT NOT NULL,
                balance_after TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                terminal_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                synced INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        logger.info("Database initialized")
        return True
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        return False
    finally:
        if 'conn' in locals() and conn:
            conn.close()

# nfc functions
def init_nfc_reader():
    import board
    import busio
    from digitalio import DigitalInOut
    from adafruit_pn532.i2c import PN532_I2C

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = DigitalInOut(board.D6)
    req_pin = DigitalInOut(board.D12)
    pn532 = PN532_I2C(i2c, debug=False, reset=reset_pin, req=req_pin)

    ic, ver, rev, support = pn532.firmware_version
    logger.info(f"PN532 firmware version: {ver}.{rev}")
    pn532.SAM_configuration()
    return pn532

def read_card_uid(pn532):
    logger.info("Waiting for card...")
    print("Tap a card when ready...")
    while True:
        try:
            uid = pn532.read_passive_target(timeout=1.0)
            if uid is not None:
                uid_hex = hexlify(uid).decode('utf-8').upper()
                logger.info(f"Card UID: {uid_hex}")
                return uid_hex
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Card read error: {e}")
            time.sleep(1)

# server communication functions
def check_server_connection():
    global server_available
    try:
        response = requests.get(f"{SERVER_URL}/health", timeout=5, verify=False)
        server_available = response.status_code == 200
        logger.info(f"Server connection: {'Connected' if server_available else 'Failed'}")
        return server_available
    except Exception as e:
        logger.debug(f"Server connection error: {e}")
        server_available = False
        return False

def reconnection_manager():
    global server_available
    while True:
        if not server_available:
            if check_server_connection():
                logger.info("Reconnected to server")
                sync_transactions()
        time.sleep(30)

def start_reconnection_manager():
    logger.info("Reconnection manager started")
    thread = threading.Thread(target=reconnection_manager, daemon=True)
    thread.start()
    logger.info("Reconnection manager thread started")

def sync_transactions():
    if not server_available:
        logger.warning("Server unavailable, skipping sync")
        return False

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE synced = 0")
        transactions = cursor.fetchall()

        if not transactions:
            return True

        success_count = 0
        for tx in transactions:
            # decrypt transaction data for syncing
            try:
                payload = {
                    "uid": tx["account_id"],
                    "amount": float(decrypt_data(tx["amount"])) if ENCRYPTION_ENABLED else float(tx["amount"]),
                    "balance_before": float(decrypt_data(tx["balance_before"])) if ENCRYPTION_ENABLED else float(tx["balance_before"]),
                    "balance_after": float(decrypt_data(tx["balance_after"])) if ENCRYPTION_ENABLED else float(tx["balance_after"]),
                    "transaction_type": decrypt_data(tx["transaction_type"]) if ENCRYPTION_ENABLED else tx["transaction_type"],
                    "terminal_id": TERMINAL_ID,
                    "timestamp": decrypt_data(tx["timestamp"]) if ENCRYPTION_ENABLED else tx["timestamp"]
                }
            except Exception as e:
                logger.error(f"Failed to decrypt transaction {tx['id']}: {e}")
                continue

            try:
                response = requests.post(f"{SERVER_URL}/sync_transaction", json=payload, timeout=5, verify=False)
                if response.status_code == 200:
                    cursor.execute("UPDATE transactions SET synced = 1 WHERE id = ?", (tx["id"],))
                    conn.commit()
                    success_count += 1
                else:
                    logger.warning(f"Sync failed for tx {tx['id']}: {response.status_code}")
            except Exception as e:
                logger.error(f"Sync error for tx {tx['id']}: {e}")

        logger.info(f"Synced {success_count}/{len(transactions)} transactions")
        return success_count == len(transactions)
    except Exception as e:
        logger.error(f"Sync process error: {e}")
        return False
    finally:
        if 'conn' in locals() and conn:
            conn.close()

# balance management functions
def get_card_balances():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, 'r') as f:
                encrypted_balances = json.load(f)
                if ENCRYPTION_ENABLED:
                    return decrypt_json(encrypted_balances)
                return encrypted_balances
        except Exception as e:
            logger.error(f"Balance file error: {e}")
    return {}

def save_card_balances(balances):
    try:
        if ENCRYPTION_ENABLED:
            encrypted_balances = encrypt_json(balances)
        else:
            encrypted_balances = balances
            
        with open(BALANCE_FILE, 'w') as f:
            json.dump(encrypted_balances, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving balances: {e}")
        return False

def get_card_balance(card_id):
    if server_available:
        try:
            response = requests.get(f"{SERVER_URL}/get_card_balance/{card_id}",
                                    params={"terminal_id": TERMINAL_ID},
                                    verify=False)
            if response.status_code == 200:
                balance = response.json()["balance"]
                balances = get_card_balances()
                balances[card_id] = balance
                save_card_balances(balances)
                return balance
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")

    # fallback to local storage
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

# transaction functions
def register_transaction(card_id, amount, before, after):
    timestamp = datetime.datetime.now().isoformat()
    tx_type = "payment" if amount < 0 else "topup"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # encrypt sensitive data if encryption is enabled
        amount_val = encrypt_data(str(amount)) if ENCRYPTION_ENABLED else str(amount)
        before_val = encrypt_data(str(before)) if ENCRYPTION_ENABLED else str(before)
        after_val = encrypt_data(str(after)) if ENCRYPTION_ENABLED else str(after)
        tx_type_val = encrypt_data(tx_type) if ENCRYPTION_ENABLED else tx_type
        timestamp_val = encrypt_data(timestamp) if ENCRYPTION_ENABLED else timestamp
        
        cursor.execute("""
            INSERT INTO transactions (
                account_id, amount, balance_before, balance_after,
                transaction_type, terminal_id, timestamp, synced
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (card_id, amount_val, before_val, after_val, tx_type_val, TERMINAL_ID, timestamp_val, 0))
        
        conn.commit()
        logger.info(f"Transaction recorded: {card_id}, ${amount}")
        return True
    except Exception as e:
        logger.error(f"Register transaction error: {e}")
        return False
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def process_fare_payment(card_id):
    current_balance = get_card_balance(card_id)
    if current_balance < FARE_AMOUNT:
        print(f"Insufficient funds: ${current_balance:.2f}")
        return False

    new_balance = current_balance - FARE_AMOUNT
    server_synced = False

    if server_available:
        try:
            payload = {
                "uid": card_id,
                "fare": FARE_AMOUNT,
                "terminal_id": TERMINAL_ID
            }
            response = requests.post(f"{SERVER_URL}/process_payment", json=payload, timeout=5, verify=False)
            if response.status_code == 200:
                new_balance = response.json()["new_balance"]
                server_synced = True
                print(f"Payment processed. New balance: ${new_balance:.2f}")
            else:
                logger.warning(f"Server returned error: {response.status_code}")
                print("Processing payment in offline mode")
        except Exception as e:
            logger.error(f"Payment server error: {e}")
            print("Processing payment in offline mode")
    else:
        print("Processing payment in offline mode")

    if update_card_balance(card_id, new_balance):
        register_transaction(card_id, -FARE_AMOUNT, current_balance, new_balance)
        if server_synced:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE transactions SET synced = 1
                    WHERE account_id = ? AND amount = ?
                    ORDER BY timestamp DESC LIMIT 1
                """, (card_id, encrypt_data(str(-FARE_AMOUNT)) if ENCRYPTION_ENABLED else str(-FARE_AMOUNT)))
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to update sync status: {e}")
            finally:
                if 'conn' in locals() and conn:
                    conn.close()
        return True
    return False

def process_topup(card_id, amount):
    if amount <= 0:
        print("Topup must be positive")
        return False

    current_balance = get_card_balance(card_id)
    new_balance = current_balance + amount
    server_synced = False

    if server_available:
        try:
            payload = {
                "uid": card_id,
                "amount": amount,
                "terminal_id": TERMINAL_ID
            }
            response = requests.post(f"{SERVER_URL}/topup_card", json=payload, timeout=5, verify=False)
            if response.status_code == 200:
                new_balance = response.json()["new_balance"]
                server_synced = True
                print(f"Topup processed. New balance: ${new_balance:.2f}")
            else:
                logger.warning(f"Server returned error: {response.status_code}")
                print("Processing topup in offline mode")
        except Exception as e:
            logger.error(f"Topup server error: {e}")
            print("Processing topup in offline mode")
    else:
        print("Processing topup in offline mode")

    if update_card_balance(card_id, new_balance):
        register_transaction(card_id, amount, current_balance, new_balance)
        if server_synced:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE transactions SET synced = 1
                    WHERE account_id = ? AND amount = ?
                    ORDER BY timestamp DESC LIMIT 1
                """, (card_id, encrypt_data(str(amount)) if ENCRYPTION_ENABLED else str(amount)))
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to update sync status: {e}")
            finally:
                if 'conn' in locals() and conn:
                    conn.close()
        return True
    return False

# heartbeat function
def send_heartbeat():
    if not server_available:
        return False

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE synced = 0")
        pending_count = cursor.fetchone()[0]
        conn.close()

        payload = {
            "terminal_id": TERMINAL_ID,
            "pending_transactions": pending_count,
            "status": "online",
            "local_time": int(time.time())
        }

        response = requests.post(f"{SERVER_URL}/terminal_heartbeat", json=payload, timeout=2, verify=False)
        return response.status_code == 200

    except Exception as e:
        logger.debug(f"Failed to send heartbeat: {e}")
        return False

# card handling
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

# main function
def main():
    if not init_database():
        logger.error("Database init failed")
        return

    try:
        pn532 = init_nfc_reader()
    except Exception as e:
        logger.error(f"NFC init failed: {e}")
        return

    check_server_connection()
    start_reconnection_manager()
    sync_transactions()

    print(f"\nTerminal Ready")
    print(f"Server status: {'ONLINE' if server_available else 'OFFLINE'}")
    print(f"Tap card to pay ${FARE_AMOUNT:.2f}")

    try:
        last_sync_time = time.time()
        last_heartbeat_time = time.time()

        while True:
            current_time = time.time()

            # background tasks
            if current_time - last_sync_time >= 300:
                if sync_transactions():
                    last_sync_time = current_time

            if current_time - last_heartbeat_time >= 60:
                if send_heartbeat():
                    last_heartbeat_time = current_time

            # card reading
            card_id = read_card_uid(pn532)

            print(f"\nCard: {card_id}")
            print(f"Server status: {'ONLINE' if server_available else 'OFFLINE'}")
            balance = get_card_balance(card_id)
            print(f"Current balance: ${balance:.2f}")

            if process_fare_payment(card_id):
                print(f"Payment successful. Fare: ${FARE_AMOUNT:.2f}")
            else:
                print("Payment failed.")

            wait_for_card_removal(pn532, 3)

            print(f"\nServer status: {'ONLINE' if server_available else 'OFFLINE'}")
            print(f"Tap card to pay ${FARE_AMOUNT:.2f}")

    except KeyboardInterrupt:
        print("\nShutting down")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        sync_transactions()

if __name__ == "__main__":
    main()

