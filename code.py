# Board related imports
import board # Infor about the board
import terminalio
import rgbmatrix # For controlling the RGB LED Panel
import framebufferio
import adafruit_lis3dh # Accelerometer
import busio
import digitalio
import usb_cdc

# Graphic imports
import adafruit_imageload
import displayio # General drawing tools
import bitmaptools # Faster drawing to bitmap helpers
from adafruit_display_text import label, outlined_label # Efficient text on bitmaps
from adafruit_bitmap_font import bitmap_font # Custom bitmap fonts (from FontForge BDF files)

# General imports
import math # general math helpers (sin/cos/etc)
from random import randrange # random numbers
import os

# Time related imports
import supervisor
import time
import adafruit_datetime
import rtc

# WiFi imports
import ipaddress
import ssl
import wifi
import socketpool
import adafruit_requests

# Settings
DEBUG = True
WIDTH = 64
HEIGHT = 64
WIFI_CHECK_INTERVAL_SEC = 120
ISS_UPDATE_INTERVAL_SEC = 60
TIME_UPDATE_INTERVAL_SEC = 3600
AUTO_REFRESH = False

################################################################################
# Functions

def debug_print(*args, **kwargs):
    """Prevent printing when serial is disconnected"""
    if DEBUG:
        try:
            print(*args, **kwargs)
        except:
            pass

def is_wifi_connected():
    """Return True if we have a valid IP address, False otherwise."""
    try:
        return wifi.radio.ipv4_address is not None
    except Exception:
        return False

def reconnect_wifi(max_retries=3, delay=5):
    """Attempt to reconnect to Wi-Fi if disconnected."""
    for attempt in range(1, max_retries + 1):
        try:
            debug_print(f"Reconnecting Wi-Fi (attempt {attempt})...")
            wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
            debug_print("Reconnected:", wifi.radio.ipv4_address)
            return True
        except Exception as e:
            debug_print("Reconnect failed:", e)
            time.sleep(delay)
    debug_print("Failed to reconnect after multiple attempts.")
    return False

def get_iss_position(requests):
    """
    Fetch the current ISS position from the API
    Returns: (latitude, longitude) tuple or None if failed
    """
    url = "http://api.open-notify.org/iss-now.json"

    for attempt in range(3):  # retry up to 3 times
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                lat = float(data['iss_position']['latitude'])
                lon = float(data['iss_position']['longitude'])
                debug_print(f"ISS Position: Lat {lat}, Lon {lon}")
                response.close()
                return (lat, lon)
            else:
                debug_print(f"ISS API returned status code: {response.status_code}")
        except Exception as e:
            debug_print(f"Error fetching ISS position (attempt {attempt + 1}): {e}")
            if not is_wifi_connected():
                reconnect_wifi()
            time.sleep(2)
    return None

def latlon_to_pixel(latitude, longitude, width=64, height=64):
    """
    Convert latitude/longitude to pixel coordinates for a Mercator projection
    
    Args:
        latitude: Latitude in degrees (-90 to 90)
        longitude: Longitude in degrees (-180 to 180)
        width: Width of the image in pixels (default 64)
        height: Height of the image in pixels (default 64)
    
    Returns:
        (x, y) tuple of pixel coordinates
    """
    # Convert longitude to x coordinate
    # Longitude ranges from -180 to 180, map to 0 to width
    x = int((longitude + 180) * (width / 360))
    
    # Convert latitude to y coordinate using Mercator projection
    # Mercator uses: y = ln(tan(π/4 + lat/2))
    lat_rad = latitude * math.pi / 180
    mercator_y = math.log(math.tan(math.pi / 4 + lat_rad / 2))
    
    # Normalize to pixel coordinates
    # Typical Mercator spans from about -π to π in y
    # Map this to 0 to height (inverted because y=0 is at top)
    y = int((1 - (mercator_y / math.pi)) * (height / 2))
    
    # Clamp to valid range
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    
    return (x, y)

def get_time_from_api(requests, local_rtc):
    """
    Fetch current time from timeapi.io
    Returns: True if successful, False otherwise
    """
    url = "https://www.timeapi.io/api/timezone/zone?timeZone=America%2FLos_Angeles"

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                timestring = response.json()['currentLocalTime']
                timestring = timestring.split('.')[0]
                local_rtc.datetime = adafruit_datetime.datetime.fromisoformat(timestring).timetuple()
                debug_print(f"Updated time from API to: {timestring}")
                return True
            else:
                debug_print(f"ISS API returned status code: {response.status_code}")
        except Exception as e:
            debug_print(f"Error fetching ISS position (attempt {attempt + 1}): {e}")
            if not is_wifi_connected():
                reconnect_wifi()
            time.sleep(2)
    return None

################################################################################
# Main

def main():
    # WiFi checker
    last_wifi_check = 0

    # ISS tracking variables
    iss_lat = None
    iss_lon = None
    last_iss_update = 0

    # Time update variables
    last_time_update = 0
    last_time_display_update = 0

    # Realtime clock
    local_rtc = rtc.RTC()

    # Hardware button setup
    down_button = digitalio.DigitalInOut(board.BUTTON_DOWN)
    down_button.direction = digitalio.Direction.INPUT
    down_button.pull = digitalio.Pull.UP # Value False when button presed
    up_button = digitalio.DigitalInOut(board.BUTTON_UP)
    up_button.direction = digitalio.Direction.INPUT
    up_button.pull = digitalio.Pull.UP # Value False when button presed

    # Display setup
    displayio.release_displays()

    # RGB Matrix initialization
    matrix = rgbmatrix.RGBMatrix(
        width=WIDTH, height=HEIGHT, bit_depth=5,
        rgb_pins=[board.MTX_R1, board.MTX_G1, board.MTX_B1,
                board.MTX_R2, board.MTX_G2, board.MTX_B2],
        addr_pins=[board.MTX_ADDRA, board.MTX_ADDRB, board.MTX_ADDRC,
                board.MTX_ADDRD, board.MTX_ADDRE],
        clock_pin=board.MTX_CLK, latch_pin=board.MTX_LAT, output_enable_pin=board.MTX_OE,
        doublebuffer=False)

    # Initialize display
    display = framebufferio.FramebufferDisplay(
        matrix,
        auto_refresh=AUTO_REFRESH,
        rotation=90,
    )    
    display.brightness = 1 # Current implementation is 0 = off anything non-zero value = full brightness

    # Main "Full color" bitmap we use for drawing onto
    bitmap = displayio.Bitmap(WIDTH,HEIGHT,65535)
    cc = displayio.ColorConverter(input_colorspace=displayio.Colorspace.RGB565)
    tg1 = displayio.TileGrid(bitmap,pixel_shader=cc)
    g1 = displayio.Group(scale=1)
    g1.append(tg1)

    # Default palette for sprite mode - may get overwritten in special initialization
    palette = displayio.Palette(256)
    palette[0] = 0x0
    palette[1] = 0xf80000
    palette[2] = 0xf8f8f8
    palette[3] = 0xf8
    palette[4] = 0xf8f8
    palette[5] = 0xf8c8e0
    palette[6] = 0xf89800
    palette[7] = 0xf8f800
    palette[8] = 0x983800
    palette[9] = 0xf800
    palette[10] = 0xf8e0c0
    palette[11] = 0xf8e860
    palette[12] = 0xffff80
    palette[13] = 0xff0000
    palette[14] = 0xf8e0f8
    palette.make_transparent(0)

    # Palettized bitmap layer for sprite mode - in front of regular bitmap layer
    palettized_bitmap = displayio.Bitmap(WIDTH,HEIGHT,256)
    tg2 = displayio.TileGrid(palettized_bitmap,pixel_shader=palette)
    #g1.append(tg2) # Comment this line out if not using this layer (for faster updates)

    # Set root display object
    display.root_group = g1
    # display.refresh()

    # Perfomance tracking
    fps_sum = 0
    fps_samples = 0
    fps_start = -1
    last_print_time = 0
    last_fps = 0

    # Load the world map image
    try:
        world_map_bitmap, world_map_palette = adafruit_imageload.load("/world_map.png",
                                                                        bitmap=displayio.Bitmap,
                                                                        palette=displayio.Palette)
        debug_print("World map loaded successfully")
    except Exception as e:
        debug_print(f"Error loading world map: {e}")
        world_map_bitmap = None

    # Perfomance tracking
    fps_sum = 0
    fps_start = supervisor.ticks_ms()
    last_print_time = 0

    # Connect to the Internet
    debug_print(f"My MAC address: {[hex(i) for i in wifi.radio.mac_address]}") # show our MAC

    # Show which WiFi networks we can see (comment this back in to survey the wifi in your area)
    # wifi.radio.stop_scanning_networks()
    # for network in wifi.radio.start_scanning_networks():
    #     debug_print("\t%s\t\tRSSI: %d\tChannel: %d" % (str(network.ssid, "utf-8"),
    #                                              network.rssi, network.channel))
    # wifi.radio.stop_scanning_networks() # stop scanning

    # Connect the specified access point in the TOML file
    debug_print( f"WiFi Connecting to {os.getenv("CIRCUITPY_WIFI_SSID")}" )
    try:
        wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"),os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    except Exception as e:
        debug_print(f"Could not connect to {os.getenv("CIRCUITPY_WIFI_SSID")}" )
        
    # Set up objects so we can do Web API requests
    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())

    # Grab the current time via the timeapi.io API (set a timeout so we don't wait forever)
    debug_print( f"CircuitPython thinks the time/date is {adafruit_datetime.datetime.today()}\nGetting time from API" )
    try:
        response = requests.request("GET", "https://www.timeapi.io/api/timezone/zone?timeZone=America%2FLos_Angeles", timeout=2 )
        """ example response
        {
        "timeZone": "America/Los_Angeles",
        "currentLocalTime": "2025-08-18T17:15:28.519488",
        ...
        }
        """
        if response.status_code == 200:
            debug_print("Got time from API - Parsing")
            # parse the JSON
        #    debug_print( f"Response was:\n{response.json()}" )
            timestring = response.json()['currentLocalTime']
            # knock off the fractional seconds to prevent exceptions in the parser
            timestring = timestring.split('.')[0]
            local_rtc.datetime = adafruit_datetime.datetime.fromisoformat(timestring).timetuple() # Convert to actual datetime object and set the controller's time
            debug_print( f"Updating local timesource to {timestring}" )
        else:
            debug_print("Get response was {response}")
    except Exception as e:
        debug_print(f"API check failed {e}")

    # Use the main bitmap for drawing
    # Clear the existing group and set up fresh
    g1 = displayio.Group(scale=1)
    cc = displayio.ColorConverter(input_colorspace=displayio.Colorspace.RGB565)
    tg1 = displayio.TileGrid(bitmap, pixel_shader=cc)
    g1.append(tg1)
    display.root_group = g1

    # Get initial time
    debug_print(f"Getting initial time from API")
    get_time_from_api(requests, local_rtc)

    # After your display setup and before the main loop, set up the text label
    time_font = bitmap_font.load_font("/ArcadeNormal-8.bdf")
    time_text_area = label.Label(time_font, text="--:--", color=0x400000)
    time_text_area.x = 32 - (time_text_area.width // 2)  # Center horizontally
    time_text_area.y = 58  # Near bottom (64 - 6 pixels from bottom)

    # Add the text to your display group
    g1.append(time_text_area)

    # Force a display refresh to test if text is visible
    time_text_area.text = "12:34"
    time_text_area.x = 32 - (time_text_area.width // 2)
    # display.refresh()
    debug_print(f"Text area set to: {time_text_area.text}, position: ({time_text_area.x}, {time_text_area.y})")

    # Main Loop
    while True:
        # Timing stuff for FPS calculations
        ticks = supervisor.ticks_ms()
        delta = (ticks - fps_start) / 1000
        fps_start = ticks

        if delta < 0 or delta > 1000:
            delta = 0.016

        # Check for WiFi (and reconnect if needed)
        if (ticks - last_wifi_check) > (WIFI_CHECK_INTERVAL_SEC * 1000):
            debug_print("Performing WiFi check")
            if not is_wifi_connected():
                debug_print("WiFi connection lost. Reconnecting.")
                reconnect_wifi()
            last_wifi_check = ticks

        # Update ISS position every minute
        if (ticks - last_iss_update) > (ISS_UPDATE_INTERVAL_SEC * 1000):
            debug_print("Requesting ISS location")
            iss_position = get_iss_position(requests)
            if iss_position:
                iss_lat, iss_lon = iss_position
            last_iss_update = ticks
            debug_print(f"ISS lat: {iss_lat}, lon: {iss_lon}")

        # Update time from API every hour
        if (ticks - last_time_update) > (TIME_UPDATE_INTERVAL_SEC * 1000):
            debug_print("Requesting time update")
            get_time_from_api(requests, local_rtc)
            last_time_update = ticks

        # Update time display on screen every second
        if (ticks - last_time_display_update) > 1000:
            current_time = local_rtc.datetime
            time_string = f"{current_time.tm_hour:02}:{current_time.tm_min:02}"
            time_text_area.text = time_string
            time_text_area.x = 32 - (time_text_area.width // 2)
            last_time_display_update = ticks

        # Clear the bitmap
        bitmap.fill(0)
        
        # Copy the map to the bitmap as the base layer
        if world_map_bitmap:
            bitmaptools.blit(bitmap, world_map_bitmap, 0, 0)

        # Draw ISS position if we have it
        if iss_lat is not None and iss_lon is not None:
            # Convert lat/lon to x/y on the mercator projection map
            x, y = latlon_to_pixel(iss_lat, iss_lon)

            # Optional: draw a small cross hair
            bitmaptools.draw_line(bitmap, x-1, y, x+1, y, 0x0800)
            bitmaptools.draw_line(bitmap, x, y-1, x, y+1, 0x0800)

            # Draw a bright red circle for the ISS
            bitmap[x, y] = 0xF800

        # Manually update the display
        if not display.auto_refresh:
            display.refresh()
        
        # FPS tracking
        fps_sum += 1
        if ticks - last_print_time > 1000:
            debug_print(f"FPS: {fps_sum}")
            fps_sum = 0
            last_print_time = ticks

        # Delay if we're auto-refreshing
        if display.auto_refresh:
            time.sleep(0.05)

# Entrypoint: call main
if __name__ == "__main__":
    main()