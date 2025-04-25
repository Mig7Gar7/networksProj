from OpenSSL import crypto
import os

def create_cert():
    # makes certs directory if it doesn't exist
    if not os.path.exists('certs'):
        os.makedirs('certs')
        
    # key pair
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 2048)
    
    # self-signed cert
    cert = crypto.X509()
    cert.get_subject().C = "US"
    cert.get_subject().ST = "State"
    cert.get_subject().L = "City"
    cert.get_subject().O = "Bus Ticketing"
    cert.get_subject().CN = "localhost"
    cert.set_serial_number(1000)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(10*365*24*60*60) 
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, 'sha256')
    
    # certificate
    with open("certs/server.crt", "wb") as f:
        f.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        
    # private key
    with open("certs/server.key", "wb") as f:
        f.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))
        
    print("Certificate generated: certs/server.crt")
    print("Private key generated: certs/server.key")

if __name__ == "__main__":
    create_cert()
