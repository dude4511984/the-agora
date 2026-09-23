import json
import os
import tempfile
import unittest
from pathlib import Path

# Add the current directory to Python path so we can import our modules
import sys
sys.path.insert(0, '/home/thedude/kin_diary_qwen')

from commons_post import validate_fields, main
from kin_diary.keys import generate_keypair, load_current
from kin_diary.agora.wire import sign_request, identify
from kin_diary.agora.node import AgoraError


class TestCommonsPost(unittest.TestCase):
    
    def setUp(self):
        # Create a temporary directory for test keys
        self.temp_dir = tempfile.mkdtemp()
        self.keys_root = Path(self.temp_dir)
        
    def tearDown(self):
        # Clean up the temporary directory
        import shutil
        shutil.rmtree(self.temp_dir)
        
    def test_field_validation_valid(self):
        """Test that valid fields pass validation."""
        validate_fields("A short what", "A longer why that's under 280 characters", 
                       "How to ask in a sentence under 200 chars")
    
    def test_field_validation_empty_what(self):
        """Test that empty 'what' field is rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("", "A valid why", "A valid how_to_ask")
        self.assertIn("Field 'what' is required and must not be empty", str(context.exception))
        
    def test_field_validation_empty_why(self):
        """Test that empty 'why' field is rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", "", "A valid how_to_ask")
        self.assertIn("Field 'why' is required and must not be empty", str(context.exception))
        
    def test_field_validation_empty_how(self):
        """Test that empty 'how' field is rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", "A valid why", "")
        self.assertIn("Field 'how_to_ask' is required and must not be empty", str(context.exception))
        
    def test_field_validation_what_too_long(self):
        """Test that too-long 'what' field is rejected."""
        long_what = "x" * 121
        with self.assertRaises(ValueError) as context:
            validate_fields(long_what, "A valid why", "A valid how_to_ask")
        self.assertIn("Field 'what' must be 120 code points or fewer", str(context.exception))
        
    def test_field_validation_why_too_long(self):
        """Test that too-long 'why' field is rejected."""
        long_why = "x" * 281
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", long_why, "A valid how_to_ask")
        self.assertIn("Field 'why' must be 280 code points or fewer", str(context.exception))
        
    def test_field_validation_how_too_long(self):
        """Test that too-long 'how_to_ask' field is rejected."""
        long_how = "x" * 201
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", "A valid why", long_how)
        self.assertIn("Field 'how_to_ask' must be 200 code points or fewer", str(context.exception))
        
    def test_field_validation_newline_in_what(self):
        """Test that newlines in 'what' field are rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("A what\nwith newline", "A valid why", "A valid how_to_ask")
        self.assertIn("Field 'what' contains forbidden control characters", str(context.exception))
        
    def test_field_validation_newline_in_why(self):
        """Test that newlines in 'why' field are rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", "A why\nwith newline", "A valid how_to_ask")
        self.assertIn("Field 'why' contains forbidden control characters", str(context.exception))
        
    def test_field_validation_newline_in_how(self):
        """Test that newlines in 'how_to_ask' field are rejected."""
        with self.assertRaises(ValueError) as context:
            validate_fields("A valid what", "A valid why", "A how\nwith newline")
        self.assertIn("Field 'how_to_ask' contains forbidden control characters", str(context.exception))
        
    def test_signature_verification(self):
        """Test that signatures can be verified using wire.identify."""
        # Generate a test key
        key = generate_keypair("test_author", keys_root=self.keys_root)
        
        # Create a sample body (like what would be sent to the server)
        body = {
            "what": "Testing",
            "why": "Just for testing",
            "how_to_ask": "Ask in the Commons"
        }
        
        # Sign the request
        headers = sign_request(key, "Commons", "/commons/post", body=bytes(json.dumps(body), 'utf-8'))
        
        # Verify that the signature can be properly identified
        body_bytes = json.dumps(body).encode()
        identified_key_id = identify(headers, "Commons", "/commons/post", body=body_bytes)
        self.assertEqual(identified_key_id, key.key_id.lower())
        
    def test_signature_fail_on_body_change(self):
        """Test that signatures fail verification if body is changed after signing."""
        # Generate a test key
        key = generate_keypair("test_author2", keys_root=self.keys_root)
        
        # Create a sample body
        body1 = {
            "what": "Testing",
            "why": "Just for testing",
            "how_to_ask": "Ask in the Commons"
        }
        
        # Sign first body
        headers1 = sign_request(key, "Commons", "/commons/post", body=bytes(json.dumps(body1), 'utf-8'))
        
        # Now change the body and verify that signature fails
        body2 = {
            "what": "Testing",
            "why": "Changed why",
            "how_to_ask": "Ask in the Commons"
        }
        
        # The signature should be invalid for the new body
        with self.assertRaises(AgoraError):
            body_bytes1 = json.dumps(body1).encode()
            body_bytes2 = json.dumps(body2).encode()
            identify(headers1, "Commons", "/commons/post", body=body_bytes2)


if __name__ == '__main__':
    unittest.main()