"""
USB Device Diagnostic Tool
Run this to see all USB devices and debug connection issues.
"""

import sys

try:
    import usb.core
    import usb.util
except ImportError:
    print("ERROR: PyUSB not installed. Run: poetry install")
    sys.exit(1)

# Try to load libusb backend
_backend = None
try:
    import libusb_package
    _backend = libusb_package.get_libusb1_backend()
    print("✓ libusb-package backend loaded successfully")
except ImportError:
    print("⚠ libusb-package not available (this may cause issues on Windows)")
except Exception as e:
    print(f"⚠ Error loading libusb backend: {e}")

print("\n" + "="*60)
print("USB DEVICE SCAN")
print("="*60)

# Find all USB devices
devices = list(usb.core.find(find_all=True, backend=_backend))

if not devices:
    print("\n❌ NO USB DEVICES FOUND!")
    print("\nPossible causes:")
    print("1. USB drivers not installed (Windows needs WinUSB/libusb drivers)")
    print("2. Device not connected")
    print("3. USB debugging not enabled on Android")
    print("4. Permission issues")
    print("\nWindows Solution:")
    print("  Install Zadig: https://zadig.akeo.ie/")
    print("  1. Connect your Android device")
    print("  2. Enable USB debugging on Android")
    print("  3. Run Zadig as Administrator")
    print("  4. Options → List All Devices")
    print("  5. Select your Android device")
    print("  6. Install WinUSB driver")
    sys.exit(1)

print(f"\n✓ Found {len(devices)} USB device(s)\n")

for i, device in enumerate(devices, 1):
    print(f"Device #{i}:")
    print(f"  Vendor ID:  0x{device.idVendor:04X}")
    print(f"  Product ID: 0x{device.idProduct:04X}")
    
    # Try to get manufacturer and product strings
    try:
        manufacturer = usb.util.get_string(device, device.iManufacturer) if device.iManufacturer else "N/A"
        product = usb.util.get_string(device, device.iProduct) if device.iProduct else "N/A"
        print(f"  Manufacturer: {manufacturer}")
        print(f"  Product: {product}")
    except Exception as e:
        print(f"  ⚠ Could not read device strings: {e}")
    
    # Check if it's an Android device (common vendor IDs)
    android_vendors = {
        0x18D1: "Google",
        0x04E8: "Samsung",
        0x22B8: "Motorola",
        0x0BB4: "HTC",
        0x12D1: "Huawei",
        0x0FCE: "Sony",
        0x2717: "Xiaomi",
        0x2A45: "OnePlus",
        0x05C6: "Qualcomm",
    }
    
    if device.idVendor in android_vendors:
        print(f"  🤖 ANDROID DEVICE DETECTED ({android_vendors[device.idVendor]})")
        
        # Try to check AOA support
        try:
            data = device.ctrl_transfer(
                usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR,
                51,  # AOA_GET_PROTOCOL
                0, 0, 2, timeout=1000
            )
            version = data[0] | (data[1] << 8)
            print(f"  ✓ AOA Protocol Version: {version}")
        except usb.core.USBError as e:
            print(f"  ⚠ AOA check failed: {e}")
            print(f"     This might be a driver issue. Try installing WinUSB driver with Zadig.")
        except Exception as e:
            print(f"  ⚠ Unexpected error: {e}")
    
    print()

print("="*60)
print("\nIf you see your Android device above but AOA check fails:")
print("→ You need to install the WinUSB driver using Zadig")
print("\nIf you DON'T see your Android device:")
print("→ Enable USB debugging on your phone")
print("→ Check the USB cable (use a data cable, not charge-only)")
print("→ Try a different USB port")
