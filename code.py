# Board related imports
import board # Infor about the board
import terminalio
import rgbmatrix # For controlling the RGB LED Panel
import framebufferio
import adafruit_lis3dh # Accelerometer
import busio
import digitalio

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

WIDTH = 64
HEIGHT = 64

# ISS tracking variables
iss_lat = None
iss_lon = None
last_iss_update = 0
iss_update_interval_sec = 60

# Time update variables
last_time_update = 0
last_time_display_update = 0
time_update_interval_sec = 3600

# Realtime clock
local_rtc = rtc.RTC()

# Hardware button setup
down_button = digitalio.DigitalInOut(board.BUTTON_DOWN)
down_button.direction = digitalio.Direction.INPUT
down_button.pull = digitalio.Pull.UP # Value False when button presed

up_button = digitalio.DigitalInOut(board.BUTTON_UP)
up_button.direction = digitalio.Direction.INPUT
up_button.pull = digitalio.Pull.UP # Value False when button presed

# --- Display Setup --- #
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

display = framebufferio.FramebufferDisplay(matrix,auto_refresh=False, rotation=90) # Rotate 90 degrees so PCB pokes out of the top
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
display.refresh()

# Perfomance tracking
fps_sum = 0
fps_samples = 0
fps_start = -1
last_print_time = 0
last_fps = 0

# We want to control when the screen updates
display.auto_refresh = False
display.refresh()

use_wifi = True

if use_wifi:
    # Get connected to the internet!
    print(f"My MAC address: {[hex(i) for i in wifi.radio.mac_address]}") # show our MAC

    # Show which WiFi networks we can see (comment this back in to survey the wifi in your area)
    # wifi.radio.stop_scanning_networks()
    # for network in wifi.radio.start_scanning_networks():
    #     print("\t%s\t\tRSSI: %d\tChannel: %d" % (str(network.ssid, "utf-8"),
    #                                              network.rssi, network.channel))
    # wifi.radio.stop_scanning_networks() # stop scanning

    # Connect the specified access point in the TOML file
    print( f"WiFi Connecting to {os.getenv("CIRCUITPY_WIFI_SSID")}" )
    try:
        wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"),os.getenv("CIRCUITPY_WIFI_PASSWORD"))
    except Exception as e:
        print(f"Could not connect to {os.getenv("CIRCUITPY_WIFI_SSID")}" )
        
    # Set up objects so we can do Web API requests
    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())

    # Grab the current time via the timeapi.io API (set a timeout so we don't wait forever)
    print( f"CircuitPython thinks the time/date is {adafruit_datetime.datetime.today()}\nGetting time from API" )
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
            print("Got time from API - Parsing")
            # parse the JSON
        #    print( f"Response was:\n{response.json()}" )
            timestring = response.json()['currentLocalTime']
            # knock off the fractional seconds to prevent exceptions in the parser
            timestring = timestring.split('.')[0]
            local_rtc.datetime = adafruit_datetime.datetime.fromisoformat(timestring).timetuple() # Convert to actual datetime object and set the controller's time
            print( f"Updating local timesource to {timestring}" )
        else:
            print("Get response was {response}")
    except Exception as e:
        print(f"API check failed {e}")

# Load the world map image
try:
    world_map_bitmap, world_map_palette = adafruit_imageload.load("/world_map.png",
                                                                    bitmap=displayio.Bitmap,
                                                                    palette=displayio.Palette)
    print("World map loaded successfully")
except Exception as e:
    print(f"Error loading world map: {e}")
    world_map_bitmap = None

def get_iss_position(requests):
    """
    Fetch the current ISS position from the API
    Returns: (latitude, longitude) tuple or None if failed
    """
    try:
        response = requests.get("http://api.open-notify.org/iss-now.json", timeout=5)
        if response.status_code == 200:
            data = response.json()
            lat = float(data['iss_position']['latitude'])
            lon = float(data['iss_position']['longitude'])
            print(f"ISS Position: Lat {lat}, Lon {lon}")
            return (lat, lon)
        else:
            print(f"ISS API returned status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching ISS position: {e}")
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

def get_time_from_api(requests):
    """
    Fetch current time from timeapi.io
    Returns: True if successful, False otherwise
    """
    try:
        response = requests.request("GET", "https://www.timeapi.io/api/timezone/zone?timeZone=America%2FLos_Angeles", timeout=5)
        if response.status_code == 200:
            print("Got time from API - Parsing")
            timestring = response.json()['currentLocalTime']
            timestring = timestring.split('.')[0]
            local_rtc.datetime = adafruit_datetime.datetime.fromisoformat(timestring).timetuple()
            print(f"Updated time to: {timestring}")
            return True
        else:
            print(f"Time API returned status code: {response.status_code}")
            return False
    except Exception as e:
        print(f"Time API check failed: {e}")
        return False

# Use the main bitmap for drawing
# Clear the existing group and set up fresh
g1 = displayio.Group(scale=1)
cc = displayio.ColorConverter(input_colorspace=displayio.Colorspace.RGB565)
tg1 = displayio.TileGrid(bitmap, pixel_shader=cc)
g1.append(tg1)
display.root_group = g1

# Get initial time
if use_wifi:
    print(f"Getting initial time from API")
    get_time_from_api(requests)

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
display.refresh()
print(f"Text area set to: {time_text_area.text}, position: ({time_text_area.x}, {time_text_area.y})")

# Perfomance tracking
fps_sum = 0
fps_start = supervisor.ticks_ms()
last_print_time = 0

display.auto_refresh = False

# Main Loop
while True:
    # Timing stuff for FPS calculations
    ticks = supervisor.ticks_ms()
    delta = (ticks-fps_start)/1000
    fps_start = ticks

    if delta < 0 or delta > 1000:
        delta = 0.016

    # Update ISS position every minute
    if use_wifi and (ticks - last_iss_update) > (iss_update_interval_sec * 1000):
        iss_position = get_iss_position(requests)
        if iss_position:
            iss_lat, iss_lon = iss_position
        last_iss_update = ticks
        print(f"ISS lat: {iss_lat}, lon: {iss_lon}")

    # Update time from API every hour
    if use_wifi and (ticks - last_time_update) > (time_update_interval_sec * 1000):
        get_time_from_api(requests)
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

    # Update the display
    display.refresh()
    
    # FPS tracking
    fps_sum += 1
    if ticks - last_print_time > 1000:
        # print(f"FPS: {fps_sum}")
        fps_sum = 0
        last_print_time = ticks
