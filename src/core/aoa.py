"""
AOA (Android Open Accessory) 2.0 USB host implementation.
Uses PyUSB for direct USB communication.
"""

import time
from typing import Callable, Optional

import usb.core
import usb.util

from .protocol import USB_TIMEOUT_MS


# AOA Protocol Constants
AOA_ACCESSORY_VID = 0x18D1  # Google Vendor ID
AOA_ACCESSORY_PID = 0x2D00  # Accessory mode
AOA_ACCESSORY_ADB_PID = 0x2D01  # Accessory + ADB mode

# AOA Control Request Types
AOA_GET_PROTOCOL = 51
AOA_SEND_STRING = 52
AOA_START_ACCESSORY = 53

# Accessory identification strings
MANUFACTURER = "Wolfkrypt"
MODEL = "Screen Mirror Host"
DESCRIPTION = "Wolfkrypt Screen Mirror for Android"
VERSION = "1.0"
URI = "https://wolfkrypt.com"
SERIAL = "WK001"


class AoaHost:
    """AOA 2.0 USB Host for communicating with Android device."""
    
    def __init__(self):
        self._device: Optional[usb.core.Device] = None
        self._endpoint_in: Optional[usb.core.Endpoint] = None
        self._endpoint_out: Optional[usb.core.Endpoint] = None
        self._connected = False
        self._interface = 0
        self.last_error = ""
        self._status_callback: Optional[Callable[[str], None]] = None
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    def set_status_callback(self, callback: Callable[[str], None]):
        """Set callback for status updates."""
        self._status_callback = callback
    
    def initialize(self) -> bool:
        """Initialize USB subsystem."""
        self._report_status("USB initialized (PyUSB)")
        return True
    
    def connect_to_device(self) -> bool:
        """Connect to Android device in accessory mode."""
        # First, check if device is already in accessory mode
        device = self._find_accessory_device()
        if device:
            self._report_status("Found device already in accessory mode")
        else:
            # Find an Android device and switch it to accessory mode
            android_device = self._find_android_device()
            if not android_device:
                self._set_error("No Android device found")
                return False
            
            # Get AOA protocol version
            version = self._get_aoa_protocol_version(android_device)
            if version < 1:
                self._set_error("Device does not support AOA protocol")
                return False
            self._report_status(f"AOA protocol version: {version}")
            
            # Send accessory identification
            if not self._send_accessory_strings(android_device):
                return False
            
            # Start accessory mode
            if not self._start_accessory_mode(android_device):
                return False
            
            usb.util.dispose_resources(android_device)
            self._report_status("Waiting for device to reconnect in accessory mode...")
            
            # Wait for device to reconnect as accessory
            for _ in range(30):
                time.sleep(0.1)
                device = self._find_accessory_device()
                if device:
                    break
            
            if not device:
                self._set_error("Device did not reconnect as accessory")
                return False
        
        self._device = device
        
        # Claim the interface and find endpoints
        if not self._claim_interface():
            return False
        
        if not self._find_bulk_endpoints():
            return False
        
        self._connected = True
        self._report_status("Connected to Android device")
        return True
    
    def disconnect(self):
        """Disconnect from the device."""
        self._connected = False
        if self._device:
            try:
                usb.util.dispose_resources(self._device)
            except Exception:
                pass
            self._device = None
        self._report_status("Disconnected")
    
    def write(self, data: bytes) -> bool:
        """Write data to the device."""
        if not self._connected or not self._endpoint_out:
            return False
        
        try:
            written = self._endpoint_out.write(data, timeout=USB_TIMEOUT_MS)
            return written == len(data)
        except usb.core.USBError as e:
            self._set_error(f"USB write error: {e}")
            return False
    
    def read(self, max_length: int, timeout_ms: int = USB_TIMEOUT_MS) -> Optional[bytes]:
        """Read data from the device."""
        if not self._connected or not self._endpoint_in:
            return None
        
        try:
            data = self._endpoint_in.read(max_length, timeout=timeout_ms)
            return bytes(data)
        except usb.core.USBTimeoutError:
            return bytes()  # Timeout is not an error
        except usb.core.USBError as e:
            self._set_error(f"USB read error: {e}")
            return None
    
    def _find_android_device(self) -> Optional[usb.core.Device]:
        """Find an Android device that supports AOA."""
        devices = usb.core.find(find_all=True)
        
        for device in devices:
            try:
                version = self._get_aoa_protocol_version(device)
                if version >= 1:
                    self._report_status(
                        f"Found Android device: VID=0x{device.idVendor:04X} "
                        f"PID=0x{device.idProduct:04X}"
                    )
                    return device
            except Exception:
                continue
        
        return None
    
    def _find_accessory_device(self) -> Optional[usb.core.Device]:
        """Find device already in accessory mode."""
        device = usb.core.find(idVendor=AOA_ACCESSORY_VID, idProduct=AOA_ACCESSORY_PID)
        if device:
            return device
        
        # Try with ADB PID
        return usb.core.find(idVendor=AOA_ACCESSORY_VID, idProduct=AOA_ACCESSORY_ADB_PID)
    
    def _get_aoa_protocol_version(self, device: usb.core.Device) -> int:
        """Get AOA protocol version from device."""
        try:
            data = device.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR,
                AOA_GET_PROTOCOL,
                0, 0, 2, timeout=1000
            )
            return data[0] | (data[1] << 8)
        except Exception:
            return -1
    
    def _send_accessory_strings(self, device: usb.core.Device) -> bool:
        """Send accessory identification strings."""
        strings = [
            (0, MANUFACTURER),
            (1, MODEL),
            (2, DESCRIPTION),
            (3, VERSION),
            (4, URI),
            (5, SERIAL),
        ]
        
        for index, string in strings:
            try:
                data = (string + '\0').encode('utf-8')
                device.ctrl_transfer(
                    usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR,
                    AOA_SEND_STRING,
                    0, index, data, timeout=1000
                )
            except usb.core.USBError as e:
                self._set_error(f"Failed to send accessory string {index}: {e}")
                return False
        
        self._report_status("Sent accessory identification strings")
        return True
    
    def _start_accessory_mode(self, device: usb.core.Device) -> bool:
        """Start accessory mode on the device."""
        try:
            device.ctrl_transfer(
                usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR,
                AOA_START_ACCESSORY,
                0, 0, None, timeout=1000
            )
            self._report_status("Accessory mode started")
            return True
        except usb.core.USBError as e:
            self._set_error(f"Failed to start accessory mode: {e}")
            return False
    
    def _claim_interface(self) -> bool:
        """Claim the USB interface."""
        if not self._device:
            return False
        
        try:
            # Detach kernel driver if needed (Linux)
            if self._device.is_kernel_driver_active(self._interface):
                self._device.detach_kernel_driver(self._interface)
            
            usb.util.claim_interface(self._device, self._interface)
            return True
        except usb.core.USBError as e:
            self._set_error(f"Failed to claim interface: {e}")
            return False
    
    def _find_bulk_endpoints(self) -> bool:
        """Find bulk IN and OUT endpoints."""
        if not self._device:
            return False
        
        cfg = self._device.get_active_configuration()
        intf = cfg[(0, 0)]
        
        self._endpoint_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
        )
        
        self._endpoint_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
        )
        
        if not self._endpoint_in or not self._endpoint_out:
            self._set_error("Failed to find bulk endpoints")
            return False
        
        self._report_status(
            f"Found endpoints: IN=0x{self._endpoint_in.bEndpointAddress:02X} "
            f"OUT=0x{self._endpoint_out.bEndpointAddress:02X}"
        )
        return True
    
    def _set_error(self, error: str):
        """Set error message."""
        self.last_error = error
        print(f"[AoaHost] Error: {error}")
    
    def _report_status(self, status: str):
        """Report status message."""
        print(f"[AoaHost] {status}")
        if self._status_callback:
            self._status_callback(status)
