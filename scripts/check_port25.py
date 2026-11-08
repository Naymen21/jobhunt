"""Check whether this machine can open outbound TCP connections on port 25.

Compares port 25 against 587/465 on the same mail hosts so a failure can be
attributed to a port block rather than a dead network.
"""
import socket
import time

HOSTS = [
    "gmail-smtp-in.l.google.com",                    # Google inbound MX
    "mx1.smtp.goog",                                 # Google Workspace MX
    "microsoft-com.mail.protection.outlook.com",     # Microsoft inbound MX
    "mx.zoho.com",
]
PORTS = [25, 587, 465]
TIMEOUT = 8


def probe(host, port):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as s:
            s.settimeout(TIMEOUT)
            banner = b""
            if port in (25, 587):
                try:
                    banner = s.recv(200)
                except socket.timeout:
                    banner = b"(no banner within timeout)"
            ms = int((time.monotonic() - start) * 1000)
            return True, f"OPEN  {ms}ms  {banner.decode(errors='replace').strip()[:90]}"
    except socket.timeout:
        return False, "TIMEOUT (packets silently dropped - typical ISP block)"
    except ConnectionRefusedError:
        return False, "REFUSED (RST - something actively rejected it)"
    except OSError as e:
        return False, f"ERROR {e}"


if __name__ == "__main__":
    results = {}
    for host in HOSTS:
        for port in PORTS:
            ok, msg = probe(host, port)
            results.setdefault(port, []).append(ok)
            print(f"{host:45} :{port:<4} {msg}")
    print()
    for port in PORTS:
        print(f"port {port}: {sum(results[port])}/{len(results[port])} hosts reachable")
    if not any(results[25]):
        print("\nVERDICT: outbound port 25 is BLOCKED from this machine.")
    else:
        print("\nVERDICT: outbound port 25 is OPEN.")
