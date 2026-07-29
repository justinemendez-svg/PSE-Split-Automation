# Set up your own Japan Overlay AE Review & Update

Same tool as `http://localhost:8771/`, running on your own laptop.

**Open Claude Code and paste this. That's it — Claude does the rest.**

```text
Set up the "Japan Overlay AE Review & Update" dashboard on my Mac. Do all of it,
and stop only if you need me to log in:

1. sf --version   (if missing: brew install sf)
2. sf org login web --alias prod --instance-url https://zendesk.my.salesforce.com
   — wait for me to finish the browser login.
3. cd ~/Desktop && git clone https://github.com/justinemendez-svg/Japan-Overlay-AE.git && cd Japan-Overlay-AE
4. python3 serve_local.py --org prod
5. Tell me to open http://localhost:8771/ and keep this terminal open.
```

---

- Log in when the browser opens (step 2). Nothing else needed from you.
- Keep the terminal open — the page only works while the server runs. Closed it? Just say *"start the server again."*
- Anything breaks? Paste the error to Claude.
