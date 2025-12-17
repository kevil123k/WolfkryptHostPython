"""
Ed25519 authentication module for Wolfkrypt.
Uses PyNaCl (libsodium) for cryptographic operations.
"""

import base64
import re
from pathlib import Path
from typing import Optional

from nacl.signing import SigningKey
from nacl.exceptions import CryptoError


class Authenticator:
    """Handles Ed25519 authentication with the Android device."""
    
    def __init__(self):
        self._signing_key: Optional[SigningKey] = None
        self._key_loaded = False
        self.last_error = ""
    
    @property
    def is_key_loaded(self) -> bool:
        return self._key_loaded
    
    def load_private_key(self, pem_path: str) -> bool:
        """Load Ed25519 private key from PEM file."""
        try:
            path = Path(pem_path)
            if not path.exists():
                self.last_error = f"Private key file not found: {pem_path}"
                return False
            
            pem_data = path.read_text()
            return self.load_private_key_from_memory(pem_data)
        except Exception as e:
            self.last_error = f"Failed to load private key: {e}"
            return False
    
    def load_private_key_from_memory(self, pem_data: str) -> bool:
        """Load Ed25519 private key from PEM string."""
        try:
            seed = self._parse_private_key_pem(pem_data)
            if seed is None:
                return False
            
            self._signing_key = SigningKey(seed)
            self._key_loaded = True
            print("[Auth] Private key loaded successfully")
            return True
        except Exception as e:
            self.last_error = f"Failed to parse private key: {e}"
            return False
    
    def _parse_private_key_pem(self, pem: str) -> Optional[bytes]:
        """Parse Ed25519 private key from PKCS#8 PEM format."""
        # Find base64 content between headers
        begin_marker = "-----BEGIN PRIVATE KEY-----"
        end_marker = "-----END PRIVATE KEY-----"
        
        if begin_marker not in pem or end_marker not in pem:
            self.last_error = "Invalid PEM format"
            return None
        
        start = pem.find(begin_marker) + len(begin_marker)
        end = pem.find(end_marker)
        base64_data = pem[start:end]
        
        # Remove whitespace
        base64_data = re.sub(r'\s+', '', base64_data)
        
        # Decode base64
        try:
            der = base64.b64decode(base64_data)
        except Exception:
            self.last_error = "Failed to decode base64"
            return None
        
        # Ed25519 PKCS#8 private key has the 32-byte seed at offset 16
        if len(der) < 48:
            self.last_error = "Invalid Ed25519 private key length"
            return None
        
        # Extract the 32-byte seed
        SEED_OFFSET = 16
        seed = der[SEED_OFFSET:SEED_OFFSET + 32]
        
        return seed
    
    def sign_challenge(self, challenge: bytes) -> Optional[bytes]:
        """Sign a 32-byte challenge and return 64-byte signature."""
        if not self._key_loaded or self._signing_key is None:
            self.last_error = "Private key not loaded"
            return None
        
        if len(challenge) != 32:
            self.last_error = f"Invalid challenge size: {len(challenge)} (expected 32)"
            return None
        
        try:
            # Sign using Ed25519 (detached signature)
            signed = self._signing_key.sign(challenge)
            signature = signed.signature  # 64 bytes
            print("[Auth] Challenge signed successfully")
            return signature
        except CryptoError as e:
            self.last_error = f"Signing failed: {e}"
            return None
    
    def get_public_key(self) -> Optional[bytes]:
        """Get the 32-byte public key."""
        if not self._key_loaded or self._signing_key is None:
            return None
        return bytes(self._signing_key.verify_key)
