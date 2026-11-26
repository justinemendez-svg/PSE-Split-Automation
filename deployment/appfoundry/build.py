"""Build the hosted upload app from the canonical dashboard and rules.

Run: python3 deployment/appfoundry/build.py --output /tmp/pse-hosted-app.py
No snapshots, Salesforce credentials, or local service files are bundled.
"""
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError(f'Canonical dashboard changed; review hosted adapter: {old[:90]}')
    return value.replace(old, new, 1)


def build():
    html = (ROOT / 'docs/index.html').read_text()
    js = (ROOT / 'docs/app.js').read_text()
    html = replace_once(html, '</style>', (HERE / 'upload.css').read_text() + '\n</style>')
    html = replace_once(html, '<button class="ghost hidden" id="refreshBtn">', '<button class="ghost" id="uploadDataBtn">Upload the data</button><button class="ghost hidden" id="refreshBtn">')
    html = replace_once(html, '<button class="ghost" id="uploadDataBtn">',
                        '<a class="btn-connect" id="connectSalesforce" href="/oauth/login">Connect to Salesforce</a>'
                        '<button class="ghost hidden" id="disconnectSalesforce">Disconnect Salesforce</button>'
                        '<button class="ghost" id="uploadDataBtn">')
    notice = ('<section class="process-help"><h3>Salesforce connection and uploaded data</h3>'
              '<p>Connect to Salesforce using your own account after the Salesforce admin completes the OAuth setup. '
              'Connecting does not fetch data: click Refresh when ready. Refresh only reads Salesforce. '
              'Assignment reviews are saved in your own session; Salesforce writes require Update and confirmation. '
              'Connected sessions expire after eight hours. When disconnected, this dashboard shows the last shared upload; '
              'live reviews, Refresh and Update are unavailable. Upload the data remains available.</p>'
              '<button type="button" id="firstTimeProcessLink" class="text-link">First-time data upload instructions</button>'
              '</section>')
    html = replace_once(html, '<p>How the dashboard works</p></div>', '<p>How the dashboard works</p></div>' + notice)
    html = replace_once(html, '<script src="app.js">', (HERE / 'upload.html').read_text() + '<script src="app.js">')
    html = html.replace('local decision', 'decision in your connected session').replace('local choice', 'session choice')
    html = html.replace('local confirmation', 'review confirmation')
    html = html.replace('It does not update Salesforce. To show later Salesforce changes,',
                        'When connected, the file replaces your session snapshot; when disconnected, it replaces the shared snapshot. It does not update Salesforce. To show later Salesforce changes,')
    html = html.replace('This replaces the saved data for everyone using this AppFoundry dashboard.',
                        'Upload replaces your connected-session snapshot, or the shared snapshot when disconnected.')
    html = html.replace('Uploading replaces the saved data for everyone using this dashboard.',
                        'Uploading replaces your session snapshot when connected, or the shared snapshot when disconnected.')
    # Make upload mode visible even when loading an old or missing snapshot.
    js = replace_once(js, 'wire();\n  try {', 'wire();\n  await detectApi();\n  try {')
    js = replace_once(js, 'state.writeApi.org = health.org || "default";',
                      '''state.writeApi.org = health.org || "default";
    state.writeApi.loggedIn = !!health.loggedIn;
    state.writeApi.csrfToken = health.csrfToken || "";
    $("updateAllBtn").classList.toggle("hidden", !health.writeEnabled);
    $("connectSalesforce").classList.toggle("hidden", !!health.loggedIn);
    $("connectSalesforce").textContent = health.oauthConfigured ? "Connect to Salesforce" : "Connect to Salesforce · Setup needed";
    $("disconnectSalesforce").classList.toggle("hidden", !health.loggedIn);
    if (health.loggedIn && !state.generatedAt) {
      $("status").className = "status";
      $("status").textContent = "Connected to Salesforce. Click Refresh to load your first snapshot.";
    }''')
    js = replace_once(js, 'const canReview = review || p.retainedInactive === true;',
                      'const canReview = state.writeApi.enabled && (review || p.retainedInactive === true);')
    js = replace_once(js, "const inactiveButton = needsRvp &&", "const inactiveButton = state.writeApi.enabled && needsRvp &&")
    start = js.index('  const updateButton = ')
    end = js.index('\n', start)
    original = js[start:end].removeprefix('  const updateButton = ').removesuffix(';')
    js = js[:start] + '  const updateButton = state.writeApi.enabled ? (' + original + ') : \'<a class="btn-update" href="\' + esc(href) + \'" target="_blank" rel="noopener">Open in Salesforce →</a>\';' + js[end:]
    js = replace_once(js, 'This dashboard needs a current Salesforce snapshot. Click Refresh to load the latest data.',
                      'This dashboard needs a current snapshot. Connect and click Refresh, or use Upload the data.')
    js = replace_once(js, 'load();\n', (HERE / 'upload.js').read_text() + '\nload();\n')
    js = replace_once(js, '  $("refreshBtn").addEventListener', (HERE / 'upload-wire.js').read_text() + '  $("refreshBtn").addEventListener')
    js = replace_once(js, 'headers: { "Content-Type": "application/json" }',
                      'headers: { "Content-Type": "application/json", "X-PSE-CSRF": state.writeApi.csrfToken || "" }')
    js = replace_once(js, 'function ensureWriteLogin() { return Promise.resolve(true); }',
                      'async function ensureWriteLogin() { await detectApi(); if (!state.writeApi.loggedIn) { window.location.assign("/oauth/login"); return false; } return true; }')
    js = replace_once(js, '  $("csvBtn").addEventListener("click", exportCsv);',
                      '  $("csvBtn").addEventListener("click", exportCsv);\n  $("disconnectSalesforce").addEventListener("click", async () => { try { await postJson("/oauth/logout", {}); window.location.reload(); } catch (error) { window.alert(error.message); } });')
    js = js.replace('Saves the review locally.', 'Saves the review in your session.').replace('saves your choice locally only', 'saves your choice in this session only')
    parts = ['# Generated by deployment/appfoundry/build.py. Do not edit generated code.\nimport sys\nimport types\n']
    for name in ('workflow', 'local_scope', 'local_updates', 'snapshot', 'refresh'):
        source = (ROOT / f'{name}.py').read_text()
        parts.append(f'_module = types.ModuleType({name!r})\n_module.__file__ = {("/app/" + name + ".py")!r}\nsys.modules[{name!r}] = _module\nexec({source!r}, _module.__dict__)\n')
    parts.append(f'INDEX_HTML = {html!r}\nAPP_JS = {js!r}\n')
    backend = (HERE / 'server.py.txt').read_text()
    parts.append(backend[:backend.index("if __name__ == '__main__':")])
    parts.append((HERE / 'live.py.txt').read_text())
    parts.append("\nif __name__ == '__main__':\n    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))\n")
    return ''.join(parts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build())
    print(f'Built hosted app: {args.output}')
