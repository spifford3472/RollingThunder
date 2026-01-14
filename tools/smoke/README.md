## SMOKE TEST for MQTT

- Not a framework. Not architecture. Just:
- Python script connects to MQTT
- Publishes a fake ESP32 event
- Another Python script:
- Subscribes
- Writes state into Redis
- Logs what it did

### Step 1: Install test dependencies (host Python) ###
sudo apt install -y python3-venv python3-pip
pip install paho-mqtt redis


(We’ll formalize this later; this is just a smoke test.)

### Step 2: Create a scratch test directory ###

From repo root:

mkdir -p tools/smoke

### Step 3: MQTT publisher (fake ESP32 button) ###

Create tools/smoke/mqtt_pub.py:

import json
import time
import paho.mqtt.client as mqtt

msg = {
    "source": "esp32-control-box",
    "event": "ui/page_next",
    "ts": time.time(),
}

client = mqtt.Client()
client.connect("localhost", 1883, 60)
client.loop_start()

client.publish("rt/ui/input", json.dumps(msg))
print("Published:", msg)

time.sleep(1)
client.loop_stop()

### Step 4: MQTT → Redis consumer ###

Create tools/smoke/mqtt_to_redis.py:

import json
import time
import redis
import paho.mqtt.client as mqtt

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def on_message(client, userdata, message):
    payload = json.loads(message.payload)
    r.set("ui:last_event", json.dumps(payload))
    print("Stored in Redis:", payload)

client = mqtt.Client()
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.subscribe("rt/ui/input")
client.loop_forever()

### Step 5: Run the test ###

Terminal 1:

python tools/smoke/mqtt_to_redis.py


Terminal 2:

python tools/smoke/mqtt_pub.py


You should see:

The event printed in terminal 1

Redis updated

Verify Redis directly:

docker exec -it docker-redis-1 redis-cli get ui:last_event


If you see JSON — congratulations, RollingThunder’s nervous system works.