import asyncio
import websockets
import struct
import math
from datetime import datetime
import ssl
import math

IP = "10.142.0.2"
UDP_PORT = 8080

FRAME_SIZE = 32  # time, accel, quaternion, altitude. lat/lon
SEA_PRESSURE = 1013.25 #TODO: get a weather api

# This exists in case a normal packet accidentally has the bytes "end" in it, which would mess things up
def is_end_msg(msg):
    return msg[0] == ord("e")

def pressure_to_altitude(pressure: float) -> float:
    alt = 44305.54 * ((1-(pressure/SEA_PRESSURE)**0.190284))
    return alt

def euler_from_quaternion(x, y, z, w):
    """
    Convert a quaternion into euler angles (roll, pitch, yaw)
    roll is rotation around x in radians (counterclockwise)
    pitch is rotation around y in radians (counterclockwise)
    yaw is rotation around z in radians (counterclockwise)
    """
    t0 = +2.0 * (w * x + y * z)
    t1 = +1.0 - 2.0 * (x * x + y * y)
    roll_x = math.atan2(t0, t1)

    t2 = +2.0 * (w * y - z * x)
    t2 = +1.0 if t2 > +1.0 else t2
    t2 = -1.0 if t2 < -1.0 else t2
    pitch_y = math.asin(t2)

    t3 = +2.0 * (w * z + x * y)
    t4 = +1.0 - 2.0 * (y * y + z * z)
    yaw_z = math.atan2(t3, t4)

    return roll_x, pitch_y, yaw_z # in radians

class Frame:
    def __init__(self, time, acc_x, acc_y, acc_z, quat_i, quat_j, quat_k, quat_real,alt, lat, lon):
        self.time = time
        self.acc_x = acc_x
        self.acc_y = acc_y
        self.acc_z = acc_z
        
        roll, pitch, yaw = euler_from_quaternion(quat_i, quat_j, quat_k, quat_real)
        
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.alt = alt
        self.lat = lat
        self.lon = lon

    def to_json(self):
        return "{" + f"""
        \"time\": {self.time},
        \"acc_x\": {self.acc_x},
        \"acc_y\": {self.acc_y},
        \"acc_z\": {self.acc_z},
        \"roll\": {self.roll},
        \"pitch\": {self.pitch},
        \"yaw\": {self.yaw},
        \"alt\": {pressure_to_altitude(self.alt)},
        \"lat\": {self.lat},
        \"lon\": {self.lon},
        """.replace(" ", "") + "}"

    def to_csv(self):
        return f"{self.time},{self.acc_x},{self.acc_y},{self.acc_z},{self.roll},{self.pitch},{self.yaw},{self.alt},{self.lat},{self.lon}\n"

class EndFrame:
    def to_json(self):
        return "END"

def parse(msg):
    arr = bytearray(msg[1:])
    frames = []
    for i in range(0, len(arr), FRAME_SIZE):
        frame = []
        for j in range(0, FRAME_SIZE, 4):
            frame += [struct.unpack("f", arr[i+j:i+j+4])[0]]
        frames.append(Frame(*frame))
    return frames

class UDPProtocol:
    def __init__(self, queue):
        self.started = False
        self.queue = queue

    def connection_made(self, _):
        print("connection")

    def datagram_received(self, data, _):
        if not self.started:
            if "start" in data.decode("ascii"):
                self.started = True
                self.file = open(f"dumps/broadcast_{datetime.now().strftime('%H_%M_%S_%m_%d_%Y')}.csv", "w")
                print("starting")
            return
        
        if is_end_msg(data):
            self.file.close()
            self.started = False
            self.queue.put_nowait(EndFrame())
            print("stopped")
            return
        
        try:
            for frame in parse(data):
                self.file.write(frame.to_csv())
                self.queue.put_nowait(frame) #Won't error since the queue must have an unlimited size
        except Exception as e:
            print("Corrupted frame", e)

class Websockets:
    def __init__(self, queue):
        self.queue = queue
        self.clients = {}
        self.msgs = []
        self.msg_lock = asyncio.Lock()

    async def handler(self, websocket, path):
        print(path)
        print(websocket)
        self.clients[path] = websocket
        async with self.msg_lock:
            for msg in self.msgs:
                await websocket.send(msg.to_json())
        try:
            while True:
                data = await websocket.recv()
                print(f"{path} sent {data}") #Basically discard data
        except websockets.exceptions.ConnectionClosed:
            print(self.clients, path)
            del self.clients[path]
    
    async def broadcast(self):
        while True:
            frame = await self.queue.get()
            for client in self.clients.values():
                await client.send(frame.to_json())
            async with self.msg_lock:
                if frame.to_json() == "END":
                    self.msgs.clear()
                    print ("message buffer cleared")
                else:
                    self.msgs.append(frame)


async def main():
    queue = asyncio.Queue()
    ws = Websockets(queue)

    loop = asyncio.get_event_loop()

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain("/etc/letsencrypt/live/bohra.uk/fullchain.pem", "/etc/letsencrypt/live/bohra.uk/privkey.pem")

    await websockets.serve(ws.handler, "0.0.0.0", port = 2053, ssl=ssl_context)
    await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue),
        local_addr=(IP, UDP_PORT),
    )
    await ws.broadcast()


    while True:
        await asyncio.sleep(60*60)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
