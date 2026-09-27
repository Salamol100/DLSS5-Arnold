"""Send a Python snippet to a running Maya through its (Python) command port and print the result.

Usage:  python maya_port.py <port> <python-file-or-code>
The snippet runs in Maya's main thread in __main__; assign to a variable named RESULT to get it
back (a second request evaluates repr(RESULT)).
"""
import os
import socket
import sys

port = int(sys.argv[1])
code = sys.argv[2]
if os.path.isfile(code):
    code = open(code, encoding="utf-8").read()


def send(text, timeout=900):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(text.encode("utf-8"))
    data = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(chunk) < 65536:
                break
    except socket.timeout:
        pass
    s.close()
    return data.decode("utf-8", "replace").strip("\x00\n ")


err = send("import __main__\n__main__.RESULT = None\nexec(%r, __main__.__dict__)" % code)
if err:
    print("[maya]", err)
print(send("repr(__import__('__main__').RESULT)"))
