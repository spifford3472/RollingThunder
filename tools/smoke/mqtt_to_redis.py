import json
import redis
import paho.mqtt.client as mqtt

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def on_message(client, userdata, message):
    payload = json.loads(message.payload.decode("utf-8"))
    r.set("ui:last_event", json.dumps(payload))
    print("Stored in Redis:", payload)

client = mqtt.Client()
client.on_message = on_message
client.connect("localhost", 1883, 60)
client.subscribe("rt/ui/input")
client.loop_forever()
