# Bus Ticketing System

This repository contains the code for a bus ticketing system that allows bus terminals to process payments via NFC cards. The system supports both online and offline operations with secure data storage and synchronization.

## Features

- Secure NFC card reading for payments
- Self-signed SSL certificate generation
- Online/offline operation with automatic data synchronization
- Encrypted data storage
- MySQL database backend for the server
- SQLite database for offline terminal operation
- Secure communication between terminals and server
- Automatic reconnection handling

## Components

### 1. Server (`server.py`)

The central server component that:
- Manages the card database
- Processes payments
- Handles terminal heartbeats
- Syncs transactions from terminals
- Records all operations securely

### 2. Terminal (`terminal.py`)

The terminal client that:
- Reads NFC cards
- Processes payments locally when offline
- Syncs with server when connected
- Maintains a local database of transactions
- Encrypts sensitive data

### 3. Certificate Generator (`certGeneration.py`)

Utility to generate self-signed SSL certificates for secure communication.

## Requirements

### Hardware
- Raspberry Pi (recommended) or other Linux device
- PN532 NFC reader (connected via I2C)

### Software Dependencies
- Python 3.6+
- Flask
- MySQL Connector for Python
- OpenSSL
- Cryptography
- Adafruit PN532 library (for NFC reading)
- Requests

## Installation

1. Clone this repository:
```
git clone https://github.com/yourusername/bus-ticketing-system.git
cd bus-ticketing-system
```

2. Install required Python packages:
```
pip install flask mysql-connector-python pyOpenSSL cryptography adafruit-circuitpython-pn532 requests
```

3. Set up MySQL database (for server):
```
sudo apt install mysql-server
sudo mysql_secure_installation
sudo mysql -u root -p
```

4. In MySQL, create the database:
```sql
CREATE DATABASE bus_system;
CREATE USER 'bus_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON bus_system.* TO 'bus_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

5. Generate SSL certificates:
```
python certGeneration.py
```

## Configuration

### Server Configuration
Edit `server.py` and update the following:
- Database configuration in `DB_CONFIG`
- Encryption settings (salt and passwords)
- Server port if needed

### Terminal Configuration
Edit `terminal.py` and update:
- `SERVER_URL` to match your server's IP address
- `FARE_AMOUNT` to your standard fare amount
- Encryption settings if needed
- NFC reader pins if using a different configuration

## Running the System

### Start the Server
```
python server.py
```

The server will run on port 8443 (HTTPS) by default.

### Start the Terminal
```
python terminal.py
```

## Usage

1. The terminal will initialize and connect to the server if available
2. Tap a card on the NFC reader to process a payment
3. If using a new card, the system will automatically register it with a default balance
4. Remove the card when prompted
5. Terminal will synchronize with the server when online

## Security Features

- Self-signed SSL certificates for encrypted communication
- Data encryption using Fernet symmetric encryption
- Secure key derivation with PBKDF2
- Encrypted storage of sensitive information (balances, transactions)
- Safe offline operation with data integrity
