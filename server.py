import os
import sys
import json
import time
import logging
import datetime
from flask import Flask, request, jsonify
import mysql.connector
from mysql.connector import Error

# log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('bus_server')

app = Flask(__name__)

# db
DB_CONFIG = {
    'host': 'localhost',
    'user': '',  # use your mysql user
    'password': '',  # use your mysql passwd
    'database': 'bus_system'
}

def get_db_connection():
    """Create and return a connection to the database"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        logger.error(f"Database connection error: {e}")
        return None

def init_database():
    """Initialize the MySQL database"""
    logger.info("Initializing MySQL database...")
    
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to MySQL database")
            return False
            
        cursor = conn.cursor()
        
        # create cards table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id VARCHAR(50) PRIMARY KEY,
            balance DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        ) ENGINE=InnoDB;
        """)
        
        # create terminals table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS terminals (
            id VARCHAR(50) PRIMARY KEY,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        ) ENGINE=InnoDB;
        """)
        
        # create transactions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INT AUTO_INCREMENT PRIMARY KEY,
            account_id VARCHAR(50),
            amount DECIMAL(10, 2) NOT NULL,
            balance_before DECIMAL(10, 2),
            balance_after DECIMAL(10, 2),
            transaction_type VARCHAR(20),
            terminal_id VARCHAR(50),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            synced BOOLEAN DEFAULT TRUE,
            FOREIGN KEY (account_id) REFERENCES cards(id),
            FOREIGN KEY (terminal_id) REFERENCES terminals(id)
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
    """Make sure the terminal is registered in the database"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM terminals WHERE id = %s", (terminal_id,))
        result = cursor.fetchone()
        
        # if terminal does not exist then make one
        if not result:
            cursor.execute(
                "INSERT INTO terminals (id, last_seen) VALUES (%s, NOW())",
                (terminal_id,)
            )
            conn.commit()
            logger.info(f"Registered new terminal: {terminal_id}")
        else:
            # update last_seen
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
    """Make sure the card is registered in the database"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM cards WHERE id = %s", (card_id,))
        result = cursor.fetchone()
        
        if not result:
            cursor.execute(
                "INSERT INTO cards (id, balance) VALUES (%s, %s)",
                (card_id, initial_balance)
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
    """Get the current balance for a card"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return None
            
        cursor = conn.cursor()
        
        cursor.execute("SELECT balance FROM cards WHERE id = %s", (card_id,))
        result = cursor.fetchone()
        
        if result:
            return float(result[0])
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
    """Update a card's balance in the database"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE cards SET balance = %s WHERE id = %s",
            (new_balance, card_id)
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
    """Record a transaction in the database"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Could not connect to database")
            return False
            
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO transactions 
        (account_id, amount, balance_before, balance_after, transaction_type, terminal_id) 
        VALUES (%s, %s, %s, %s, %s, %s)""", 
        (card_id, amount, balance_before, balance_after, transaction_type, terminal_id))
        
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

# api struggle
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "ok", "timestamp": datetime.datetime.now().isoformat()})

@app.route('/register_card', methods=['POST'])
def register_card():
    """Register a new card or update an existing one"""
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
    """Get the balance for a specific card"""
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
    """Process a payment from a card"""
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
    """Add funds to a card"""
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
    """Sync a transaction from a terminal"""
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
            
        # update card balance
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
    """Get all transactions for a specific card"""
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
        
        transactions = cursor.fetchall()
        
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
    
    # run the Flask app
    app.run(host='0.0.0.0', port=8080, debug=True)
