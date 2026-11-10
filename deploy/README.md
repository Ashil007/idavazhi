# Putting Vazhi online (free, on Oracle Cloud)

This puts Vazhi on a free cloud server. It stays up when your PC is off, and
its link never changes (`https://<your-ip>.sslip.io/app`). You do steps 1–3
on the Oracle website (about 20 minutes). Step 4 is one command on your PC.

## 1. Create the Oracle Cloud account

1. Go to <https://www.oracle.com/cloud/free/> and click **Start for free**.
2. **Home region:** pick **India South (Hyderabad)** or **India West (Mumbai)**.
   You can't change it later, and free servers only run in your home region.
3. Oracle asks for a card to check who you are. It may place a small
   temporary hold. "Always Free" servers are never charged, as long as the
   account stays on the free tier (don't click "Upgrade to Pay As You Go").

## 2. Create the server

In the Oracle console, go to **☰ → Compute → Instances → Create instance**.

| Setting | Choose |
|---|---|
| Name | `vazhi` |
| Image | **Canonical Ubuntu 24.04** (click *Change image*) |
| Shape | *Change shape* → **Ampere** → `VM.Standard.A1.Flex`, **2 OCPU, 12 GB memory** (free up to 4 OCPU / 24 GB) |
| Networking | *Create new virtual cloud network*, **public subnet**, **Assign a public IPv4 address**: on |
| SSH keys | **Generate a key pair for me**, then click **Save private key** (keep this file safe: it is the key to your server) |

Click **Create**. When it shows *Running*, copy the **Public IP address**.

> **"Out of capacity"?** Free Ampere servers sometimes run out in Indian
> regions. Try another *Availability domain* on the same page, or try again
> in a few hours. The small `VM.Standard.E2.1.Micro` (AMD, 1 GB) is also free.
> It works too, but the first build is much slower.

## 3. Open the web ports

The server blocks web visitors until you allow them:

1. On the instance page, click the **subnet** link, then the **Default Security List**.
2. **Add Ingress Rules**: Source CIDR `0.0.0.0/0`, IP protocol **TCP**,
   Destination port range `80,443`. Save.

(The setup script opens the same two ports in the server's own firewall.)

## 4. Send Vazhi to the server

On your PC, in this folder:

```bash
python deploy/push.py <public-ip> --key C:\Users\<you>\Downloads\ssh-key-XXXX.key
```

The first run takes 30–60 minutes. During it, the server downloads Kerala's
OpenStreetMap data, builds the map and its own route service, and gets a free
HTTPS certificate. At the end it prints the link to share. The app already
works before the route service finishes; it uses the public route servers
until its own is ready.

**To update** after changing the code, run the same command again, without
`--key` the second time. It takes about a minute, and riders' data on the
server is kept.

## Good to know

- **Who can open it:** anyone with the link. It isn't listed anywhere, but
  share it only with your testers for now.
- **Your PC is not involved:** the server only has the app's code. Your local
  map data and `data/` (bus times, stop names) stay on your PC.
- **Idle servers:** Oracle may reclaim an Always Free server that sits almost
  completely idle for a week. Regular use keeps it alive.
- **Logs:** run `ssh -i ~/.ssh/vazhi-cloud.key ubuntu@<ip>`, then
  `journalctl -u vazhi -f`.
- **Your own domain later:** point it at the server's IP, then run
  `python deploy/push.py <ip> --domain vazhi.example.in`.
