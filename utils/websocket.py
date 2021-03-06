#!/usr/bin/python

'''
Python WebSocket library with support for "wss://" encryption.

You can make a cert/key with openssl using:
openssl req -new -x509 -days 365 -nodes -out self.pem -keyout self.pem
as taken from http://docs.python.org/dev/library/ssl.html#certificates

'''

import sys, socket, ssl, traceback
import os, resource, errno, signal # daemonizing
from base64 import b64encode, b64decode

settings = {
    'listen_host' : '',
    'listen_port' : None,
    'handler'     : None,
    'cert'        : None,
    'ssl_only'    : False,
    'daemon'      : True,
    'record'      : None, }
client_settings = {
    'b64encode'   : False,
    'seq_num'     : False, }

send_seq = 0

server_handshake = """HTTP/1.1 101 Web Socket Protocol Handshake\r
Upgrade: WebSocket\r
Connection: Upgrade\r
WebSocket-Origin: %s\r
WebSocket-Location: %s://%s%s\r
WebSocket-Protocol: sample\r
\r
"""

policy_response = """<cross-domain-policy><allow-access-from domain="*" to-ports="*" /></cross-domain-policy>\n"""

def traffic(token="."):
    sys.stdout.write(token)
    sys.stdout.flush()

def decode(buf):
    """ Parse out WebSocket packets. """
    if buf.count('\xff') > 1:
        if client_settings['b64encode']:
            return [b64decode(d[1:]) for d in buf.split('\xff')]
        else:
            # Modified UTF-8 decode
            return [d[1:].replace("\xc4\x80", "\x00").decode('utf-8').encode('latin-1') for d in buf.split('\xff')]
    else:
        if client_settings['b64encode']:
            return [b64decode(buf[1:-1])]
        else:
            return [buf[1:-1].replace("\xc4\x80", "\x00").decode('utf-8').encode('latin-1')]

def encode(buf):
    global send_seq
    if client_settings['b64encode']:
        buf = b64encode(buf)
    else:
        # Modified UTF-8 encode
        buf = buf.decode('latin-1').encode('utf-8').replace("\x00", "\xc4\x80")

    if client_settings['seq_num']:
        send_seq += 1
        return "\x00%d:%s\xff" % (send_seq-1, buf)
    else:
        return "\x00%s\xff" % buf


def do_handshake(sock):
    global client_settings, send_seq

    client_settings['b64encode'] = False
    client_settings['seq_num'] = False
    send_seq = 0

    # Peek, but don't read the data
    handshake = sock.recv(1024, socket.MSG_PEEK)
    #print "Handshake [%s]" % repr(handshake)
    if handshake.startswith("<policy-file-request/>"):
        handshake = sock.recv(1024)
        print "Sending flash policy response"
        sock.send(policy_response)
        sock.close()
        return False
    elif handshake.startswith("\x16"):
        retsock = ssl.wrap_socket(
                sock,
                server_side=True,
                certfile=settings['cert'],
                ssl_version=ssl.PROTOCOL_TLSv1)
        scheme = "wss"
        print "  using SSL/TLS"
    elif settings['ssl_only']:
        print "Non-SSL connection disallowed"
        sock.close()
        return False
    else:
        retsock = sock
        scheme = "ws"
        print "  using plain (not SSL) socket"
    handshake = retsock.recv(4096)
    req_lines = handshake.split("\r\n")
    _, path, _ = req_lines[0].split(" ")
    _, origin = req_lines[4].split(" ")
    _, host = req_lines[3].split(" ")

    # Parse client settings from the GET path
    cvars = path.partition('?')[2].partition('#')[0].split('&')
    for cvar in [c for c in cvars if c]:
        name, _, val = cvar.partition('=')
        if name not in ['b64encode', 'seq_num']: continue
        value = val and val or True
        client_settings[name] = value
        print "  %s=%s" % (name, value)

    retsock.send(server_handshake % (origin, scheme, host, path))
    return retsock

def daemonize():
    os.umask(0)
    os.chdir('/')
    os.setgid(os.getgid())  # relinquish elevations
    os.setuid(os.getuid())  # relinquish elevations

    # Double fork to daemonize
    if os.fork() > 0: os._exit(0)  # Parent exits
    os.setsid()                    # Obtain new process group
    if os.fork() > 0: os._exit(0)  # Parent exits

    # Signal handling
    def terminate(a,b): os._exit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Close open files
    maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if maxfd == resource.RLIM_INFINITY: maxfd = 256
    for fd in reversed(range(maxfd)):
        try:
            os.close(fd)
        except OSError, exc:
            if exc.errno != errno.EBADF: raise

    # Redirect I/O to /dev/null
    os.dup2(os.open(os.devnull, os.O_RDWR), sys.stdin.fileno())
    os.dup2(os.open(os.devnull, os.O_RDWR), sys.stdout.fileno())
    os.dup2(os.open(os.devnull, os.O_RDWR), sys.stderr.fileno())


def start_server():

    if settings['daemon']: daemonize()

    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind((settings['listen_host'], settings['listen_port']))
    lsock.listen(100)
    while True:
        try:
            csock = startsock = None
            print 'waiting for connection on port %s' % settings['listen_port']
            startsock, address = lsock.accept()
            print 'Got client connection from %s' % address[0]
            csock = do_handshake(startsock)
            if not csock: continue

            settings['handler'](csock)

        except Exception:
            print "Ignoring exception:"
            print traceback.format_exc()
            if csock: csock.close()
            if startsock and startsock != csock: startsock.close()
