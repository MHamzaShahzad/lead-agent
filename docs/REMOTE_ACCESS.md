# Using the dashboard from your iPhone (Tailscale)

Tailscale gives your Mac and iPhone a private network that only your own devices can join.
The dashboard is then reachable from anywhere **without** opening anything to the internet. It is free for personal use.

## One-time setup

1. **Mac:** install Tailscale from https://tailscale.com/download (or the Mac App Store), open it and sign in.
2. **iPhone:** install "Tailscale" from the App Store and sign in with the **same account**. Turn it on.
3. On the Mac, find its Tailscale address: click the Tailscale menu-bar icon (it shows an address like `100.x.y.z`), or run `tailscale ip -4`.

## Two ways to run it (pick one)

**A. Recommended: keep the app on the Mac only, let Tailscale forward to it**

```bash
HOST=127.0.0.1 ./run.sh       # app only listens on the Mac itself
tailscale serve --bg 8000     # private link for your own devices only
```

Tailscale prints a link like `https://your-mac.your-tailnet.ts.net`. Open it on your iPhone and use "Add to Home Screen".
WhatsApp replies keep working because WAHA still reaches the app on the Mac.

**B. Bind the app to the Tailscale address**

```bash
HOST=100.x.y.z ./run.sh       # use your own Tailscale address
```

Open `http://100.x.y.z:8000` on the iPhone. Catch: WAHA (in Docker) talks to the app on the Mac itself, so with this option
incoming replies may stop arriving. If that happens, use option A.

## Safety rules

- **Keep the dashboard password on** (`DASHBOARD_PASSWORD=` in `.env`). Anyone on your Tailscale network could otherwise open it.
- **Never expose port 8000 to the internet**: no router port-forwarding, no `tailscale funnel`, no ngrok-style tunnels.
  (`/webhook/waha` is open without a password because WAHA must reach it.)
- Don't invite other people to your Tailscale network unless you trust them.
- The Mac must be awake and the app running for the iPhone to connect.
- To stop sharing: `tailscale serve reset`, or just turn Tailscale off on the iPhone.
