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

time.sleep(0.5)
client.loop_stop()
