"""Deploy script - uploads Python files to server and restarts the bot.
All credentials MUST be set via environment variables."""
import paramiko
import os
import time
import sys

# ─── Deploy config (from env) ───
host = os.environ.get("DEPLOY_HOST", "")
user = os.environ.get("DEPLOY_USER", "")
password = os.environ.get("DEPLOY_PASSWORD", "")
key_file = os.environ.get("DEPLOY_KEY_FILE", "")
key_pass = os.environ.get("DEPLOY_KEY_PASS", "")

if not host or not user:
    print(
        "\n[FATAL] Deploy credentials not set!\n"
        "Set the following environment variables:\n"
        "  DEPLOY_HOST=your_server_ip\n"
        "  DEPLOY_USER=root\n"
        "  DEPLOY_PASSWORD=your_ssh_password   (or use key auth below)\n"
        "  DEPLOY_KEY_FILE=~/.ssh/id_rsa       (optional, preferred over password)\n"
        "  DEPLOY_KEY_PASS=                    (optional key passphrase)\n"
        "\nOr add them to .env file and load with:\n"
        '  from dotenv import load_dotenv; load_dotenv()\n',
        file=sys.stderr,
    )
    sys.exit(1)

local_dir = os.environ.get("DEPLOY_LOCAL_DIR", os.path.dirname(os.path.abspath(__file__)))
remote_dir = os.environ.get("DEPLOY_REMOTE_DIR", "/root/bot")

# ─── Upload changed Python files ───
def _connect():
    t = paramiko.Transport((host, 22))
    if key_file and os.path.exists(os.path.expanduser(key_file)):
        pkey = paramiko.RSAKey.from_private_key_file(os.path.expanduser(key_file), password=key_pass or None)
        t.connect(username=user, pkey=pkey)
    else:
        t.connect(username=user, password=password)
    return t

print(f"Connecting to {host}...")
t = _connect()
sftp = paramiko.SFTPClient.from_transport(t)

uploaded = 0
for root, dirs, files in os.walk(local_dir):
    if "__pycache__" in root or ".git" in root:
        continue
    rel = os.path.relpath(root, local_dir)
    for f in files:
        if not f.endswith(".py") or f == "deploy.py":
            continue
        local = os.path.join(root, f)
        remote = os.path.join(remote_dir, rel, f).replace("\\", "/")
        remote_parent = os.path.dirname(remote)
        try:
            sftp.stat(remote_parent)
        except FileNotFoundError:
            sftp.mkdir(remote_parent)
        sftp.put(local, remote)
        uploaded += 1
        print(f"  OK: {remote}")

sftp.close()
t.close()
print(f"\nUploaded {uploaded} files.")

# ─── Restart bot ───
print("Restarting bot...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
if key_file and os.path.exists(os.path.expanduser(key_file)):
    pkey = paramiko.RSAKey.from_private_key_file(os.path.expanduser(key_file), password=key_pass or None)
    c.connect(host, username=user, pkey=pkey)
else:
    c.connect(host, username=user, password=password)

c.exec_command("pkill -f '"'"'python3 main.py'"'"' 2>/dev/null; sleep 2; rm -f bot.log")
time.sleep(2)
c.exec_command(f"cd {remote_dir} && nohup python3 main.py > bot.log 2>&1 &")
time.sleep(5)

stdin, stdout, stderr = c.exec_command(f"tail -10 {remote_dir}/bot.log")
log_output = stdout.read().decode()[-1000:]
if log_output.strip():
    print("LOG:", log_output)
stderr_text = stderr.read().decode()[:500]
if stderr_text.strip():
    print("ERR:", stderr_text)

c.close()
print("Done.")
