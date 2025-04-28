import os
import sys
import json
import time
import logging
import datetime
import base64
import hashlib
from flask import Flask, request, jsonify
import mysql.connector
from mysql.connector import Error
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('bus_server')

# encryption configuration
ENCRYPTION_KEY_FILE = "server_key.key"
SALT = b'bus_server_salt_value_456'  # change for prod

def generate_encryption_key():
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as key_file:
            return key_file.read()
    else:
        password = b"server_secure_password"  # change for prod
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

# initialize encryption
try:
    encryption_key = generate_encryption_key()
    cipher = Fernet(encryption_key)
    ENCRYPTION_ENABLED = True
    print("Data encryption enabled")
except Exception as e:
    print(f"WARNING: Encryption initialization failed: {e}")
    print("Data will be stored unencrypted")
    ENCRYPTION_ENABLED = False

app = Flask(__name__)

# database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',  # your mysql user
    'password': 'password',  # your mysql password
    'database': 'bus_system'
}

# encryption functions
def encrypt_data(data):
    if not ENCRYPTION_ENABLED:
        return data
    if data is None:
        return None
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

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        logger.error(f"Database connection error: {e}")
        return None

def init_database():
    logger.info("Initializing MySQL database...")

    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to MySQL database")
            return False
            
        cursor = conn.cursor()
        
        # drop existing tables to recreate with correct types
        try:
            cursor.execute("DROP TABLE IF EXISTS transactions")
            cursor.execute("DROP TABLE IF EXISTS cards")
            cursor.execute("DROP TABLE IF EXISTS terminals")
        except Exception as e:
            logger.warning(f"Error dropping tables (this is normal for first run): {e}")
            
        # create terminals table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS terminals (
            id VARCHAR(50) PRIMARY KEY,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            pending_transactions INT DEFAULT 0
        ) ENGINE=InnoDB;
        """)
        
        # create cards table with text for encrypted balance
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id VARCHAR(50) PRIMARY KEY,
            balance TEXT NOT NULL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        ) ENGINE=InnoDB;
        """)
        
        # create transactions table with text for encrypted fields
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            account_id VARCHAR(50),
            amount TEXT NOT NULL,
            balance_before TEXT,
            balance_after TEXT,
            transaction_type TEXT,
            terminal_id VARCHAR(50),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            synced BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (account_id) REFERENCES cards(id) ON DELETE CASCADE,
            FOREIGN KEY (terminal_id) REFERENCES terminals(id) ON DELETE CASCADE
        ) ENGINE=InnoDB;
        """)
        
        conn.commit()
        logger.info("MySQL database initialized successfully")
        return True
        
    except Error as e:
        logger.error(f"MySQL initialization error: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def ensure_terminal_exists(terminal_id):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False

        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM terminals WHERE id = %s", (terminal_id,))
        result = cursor.fetchone()
        
        if not result:
            cursor.execute(
                "INSERT INTO terminals (id, last_seen) VALUES (%s, NOW())",
                (terminal_id,)
            )
            conn.commit()
            logger.info(f"Registered new terminal: {terminal_id}")
        else:
            cursor.execute(
                "UPDATE terminals SET last_seen = NOW() WHERE id = %s",
                (terminal_id,)
            )
            conn.commit()
        
        return True
    except Error as e:
        logger.error(f"Error in ensure_terminal_exists: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def ensure_card_exists(card_id, initial_balance=50.0):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False

        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM cards WHERE id = %s", (card_id,))
        result = cursor.fetchone()
        
        if not result:
            balance_value = encrypt_data(str(initial_balance)) if ENCRYPTION_ENABLED else str(initial_balance)
            
            cursor.execute(
                "INSERT INTO cards (id, balance) VALUES (%s, %s)",
                (card_id, balance_value)
            )
            conn.commit()
            logger.info(f"Registered new card: {card_id} with balance ${initial_balance}")
            return True
        return True
    except Error as e:
        logger.error(f"Error in ensure_card_exists: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def get_card_balance(card_id):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return None

        cursor = conn.cursor()
        
        cursor.execute("SELECT balance FROM cards WHERE id = %s", (card_id,))
        result = cursor.fetchone()
        
        if result:
            encrypted_balance = result[0]
            
            if ENCRYPTION_ENABLED:
                try:
                    decrypted_balance = decrypt_data(encrypted_balance)
                    return float(decrypted_balance)
                except Exception as e:
                    logger.error(f"Error decrypting balance: {e}")
                    return float(encrypted_balance)
            else:
                return float(encrypted_balance)
        else:
            return None
    except Error as e:
        logger.error(f"Error in get_card_balance: {e}")
        return None
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def update_card_balance(card_id, new_balance):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False

        cursor = conn.cursor()
        
        balance_value = encrypt_data(str(new_balance)) if ENCRYPTION_ENABLED else str(new_balance)
        
        cursor.execute(
            "UPDATE cards SET balance = %s WHERE id = %s",
            (balance_value, card_id)
        )
        conn.commit()
        
        if cursor.rowcount > 0:
            logger.info(f"Updated balance for card {card_id}: ${new_balance}")
            return True
        else:
            logger.warning(f"No card found with ID {card_id}")
            return False
    except Error as e:
        logger.error(f"Error in update_card_balance: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def record_transaction(card_id, amount, balance_before, balance_after, transaction_type, terminal_id):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False

        cursor = conn.cursor()
        
        # encrypt sensitive data if encryption is enabled
        amount_value = encrypt_data(str(amount)) if ENCRYPTION_ENABLED else str(amount)
        balance_before_value = encrypt_data(str(balance_before)) if ENCRYPTION_ENABLED else str(balance_before)
        balance_after_value = encrypt_data(str(balance_after)) if ENCRYPTION_ENABLED else str(balance_after)
        transaction_type_value = encrypt_data(transaction_type) if ENCRYPTION_ENABLED else transaction_type
        
        cursor.execute("""
        INSERT INTO transactions 
        (account_id, amount, balance_before, balance_after, transaction_type, terminal_id) 
        VALUES (%s, %s, %s, %s, %s, %s)""", 
        (card_id, amount_value, balance_before_value, balance_after_value, transaction_type_value, terminal_id))
        
        conn.commit()
        logger.info(f"Recorded transaction: card={card_id}, amount={amount}, type={transaction_type}")
        return True
    except Error as e:
        logger.error(f"Error in record_transaction: {e}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

# api hell
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "timestamp": datetime.datetime.now().isoformat()})

@app.route('/terminal_heartbeat', methods=['POST'])
def terminal_heartbeat():
    data = request.json
    
    if not data or 'terminal_id' not in data:
        return jsonify({"error": "terminal_id is required"}), 400
        
    terminal_id = data['terminal_id']
    pending_count = data.get('pending_transactions', 0)
    
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "UPDATE terminals SET last_seen = NOW(), pending_transactions = %s WHERE id = %s",
                (pending_count, terminal_id)
            )
            
            if cursor.rowcount == 0:
                cursor.execute(
                    "INSERT INTO terminals (id, last_seen, pending_transactions) VALUES (%s, NOW(), %s)",
                    (terminal_id, pending_count)
                )
            
            conn.commit()
            cursor.close()
            conn.close()
    except Exception as e:
        logger.error(f"Error updating terminal heartbeat: {e}")
    
    return jsonify({
        "status": "online",
        "timestamp": datetime.datetime.now().isoformat(),
        "server_time": int(time.time()),
        "pending_ack": pending_count > 0
    })

@app.route('/register_card', methods=['POST'])
def register_card():
    data = request.json

    if not data or 'uid' not in data:
        return jsonify({"error": "uid is required"}), 400

    card_id = data['uid']
    initial_balance = data.get('initial_balance', 50.0)
    terminal_id = data.get('terminal_id')

    if terminal_id:
        ensure_terminal_exists(terminal_id)

    if ensure_card_exists(card_id, initial_balance):
        return jsonify({
            "status": "success",
            "uid": card_id,
            "balance": initial_balance
        })
    else:
        return jsonify({"error": "Failed to register card"}), 500

@app.route('/get_card_balance/<card_id>', methods=['GET'])
def api_get_card_balance(card_id):
    terminal_id = request.args.get('terminal_id')

    if terminal_id:
        ensure_terminal_exists(terminal_id)

    balance = get_card_balance(card_id)

    if balance is not None:
        return jsonify({
            "status": "success",
            "uid": card_id,
            "balance": balance
        })
    else:
        # if card doesn't exist it gets a default balance
        if ensure_card_exists(card_id):
            return jsonify({
                "status": "success",
                "uid": card_id,
                "balance": 50.0,
                "message": "New card created with default balance"
            })
        else:
            return jsonify({"error": "Card not found and could not be created"}), 404

@app.route('/process_payment', methods=['POST'])
def process_payment():
    data = request.json

    if not data or 'uid' not in data or 'fare' not in data:
        return jsonify({"error": "uid and fare are required"}), 400

    card_id = data['uid']
    fare_amount = float(data['fare'])
    terminal_id = data.get('terminal_id')

    if terminal_id:
        ensure_terminal_exists(terminal_id)

    if not ensure_card_exists(card_id):
        return jsonify({"error": "Card registration failed"}), 500

    current_balance = get_card_balance(card_id)

    if current_balance < fare_amount:
        return jsonify({
            "status": "error",
            "message": "Insufficient funds",
            "balance": current_balance
        }), 400

    new_balance = current_balance - fare_amount
    if update_card_balance(card_id, new_balance):
        record_transaction(
            card_id, 
            -fare_amount, 
            current_balance,
            new_balance,
            "payment", 
            terminal_id
        )
        
        return jsonify({
            "status": "success",
            "uid": card_id,
            "prior_balance": current_balance,
            "fare_amount": fare_amount,
            "new_balance": new_balance
        })
    else:
        return jsonify({"error": "Failed to process payment"}), 500

@app.route('/topup_card', methods=['POST'])
def topup_card():
    data = request.json

    if not data or 'uid' not in data or 'amount' not in data:
        return jsonify({"error": "uid and amount are required"}), 400

    card_id = data['uid']
    amount = float(data['amount'])
    terminal_id = data.get('terminal_id')

    if amount <= 0:
        return jsonify({"error": "Amount must be positive"}), 400

    if terminal_id:
        ensure_terminal_exists(terminal_id)

    if not ensure_card_exists(card_id):
        return jsonify({"error": "Card registration failed"}), 500

    current_balance = get_card_balance(card_id)

    new_balance = current_balance + amount
    if update_card_balance(card_id, new_balance):
        record_transaction(
            card_id, 
            amount, 
            current_balance,
            new_balance,
            "topup", 
            terminal_id
        )
        
        return jsonify({
            "status": "success",
            "uid": card_id,
            "prior_balance": current_balance,
            "topup_amount": amount,
            "new_balance": new_balance
        })
    else:
        return jsonify({"error": "Failed to process topup"}), 500

@app.route('/sync_transaction', methods=['POST'])
def sync_transaction():
    data = request.json

    if not data or 'uid' not in data or 'amount' not in data:
        return jsonify({"error": "uid and amount are required"}), 400
        
    card_id = data['uid']
    amount = float(data['amount'])
    terminal_id = data.get('terminal_id')
    timestamp = data.get('timestamp')
    balance_before = data.get('balance_before')
    balance_after = data.get('balance_after')
    transaction_type = data.get('transaction_type', 'payment' if amount < 0 else 'topup')

    if terminal_id:
        ensure_terminal_exists(terminal_id)

    if not ensure_card_exists(card_id):
        return jsonify({"error": "Card registration failed"}), 500

    if balance_before is None or balance_after is None:
        current_balance = get_card_balance(card_id)
        
        if balance_before is None:
            balance_before = current_balance - amount
            
        if balance_after is None:
            balance_after = current_balance
            
        if not update_card_balance(card_id, balance_after):
            logger.warning(f"Failed to update card balance during sync for {card_id}")

    if record_transaction(
        card_id, 
        amount, 
        balance_before,
        balance_after,
        transaction_type, 
        terminal_id
    ):
        return jsonify({
            "status": "success",
            "message": "Transaction synced successfully"
        })
    else:
        return jsonify({"error": "Failed to sync transaction"}), 500

@app.route('/get_transactions/<card_id>', methods=['GET'])
def get_transactions(card_id):
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return jsonify({"error": "Database connection failed"}), 500

        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
        SELECT id, account_id, amount, balance_before, balance_after, transaction_type, 
               terminal_id, timestamp 
        FROM transactions 
        WHERE account_id = %s 
        ORDER BY timestamp DESC
        """, (card_id,))
        
        encrypted_transactions = cursor.fetchall()
        
        # decrypt transaction data
        transactions = []
        for tx in encrypted_transactions:
            if ENCRYPTION_ENABLED:
                try:
                    decrypted_tx = {
                        "id": tx["id"],
                        "account_id": tx["account_id"],
                        "amount": float(decrypt_data(tx["amount"])),
                        "balance_before": float(decrypt_data(tx["balance_before"])) if tx["balance_before"] else None,
                        "balance_after": float(decrypt_data(tx["balance_after"])) if tx["balance_after"] else None,
                        "transaction_type": decrypt_data(tx["transaction_type"]),
                        "terminal_id": tx["terminal_id"],
                        "timestamp": tx["timestamp"]
                    }
                    transactions.append(decrypted_tx)
                except Exception as e:
                    logger.error(f"Failed to decrypt transaction: {e}")
                    transactions.append(tx)  # use encrypted version if decryption fails
            else:
                # convert strings to numeric types without decryption
                try:
                    tx["amount"] = float(tx["amount"])
                    if tx["balance_before"]:
                        tx["balance_before"] = float(tx["balance_before"])
                    if tx["balance_after"]:
                        tx["balance_after"] = float(tx["balance_after"])
                except Exception:
                    pass
                transactions.append(tx)
        
        return jsonify({
            "status": "success",
            "uid": card_id,
            "transactions": transactions
        })
    except Error as e:
        logger.error(f"Error in get_transactions: {e}")
        return jsonify({"error": "Failed to get transactions"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    if not init_database():
        logger.error("Failed to initialize database. Exiting.")
        sys.exit(1)

    # check for ssl certificates
    cert_path = "certs/server.crt"
    key_path = "certs/server.key"
    
    if os.path.exists(cert_path) and os.path.exists(key_path):
        logger.info("Starting secure server with HTTPS")
        app.run(host='0.0.0.0', port=8443, ssl_context=(cert_path, key_path), debug=True)
    else:
        logger.warning("SSL certificates not found, starting in plain HTTP mode (not secure)")
        try:
            import certGen
            certGen.create_cert()
            logger.info("Generated new certificates")
            app.run(host='0.0.0.0', port=8443, ssl_context=(cert_path, key_path), debug=True)
        except Exception as e:
            logger.error(f"Could not generate certificates: {e}")
            app.run(host='0.0.0.0', port=8080, debug=True)
