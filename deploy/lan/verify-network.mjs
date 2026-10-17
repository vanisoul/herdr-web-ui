// Run from a Docker bridge subnet that is NOT in the LAN allowlist.
// No credentials or terminal input are sent.
import assert from 'node:assert/strict';
import http from 'node:http';
import https from 'node:https';

const [host, port = '8443', backend = '7317'] = process.argv.slice(2);
assert(host, '用法：node verify-network.mjs HOST [HTTPS_PORT] [BACKEND_PORT]');
function status(protocol, port, headers = {}) {
  return new Promise((resolve, reject) => {
    // TLS validation is performed separately by verify.py; this request sends no secret.
    const request = protocol.get({hostname: host, port, path: '/', headers, rejectUnauthorized: false}, response => {
      response.resume(); resolve(response.statusCode);
    });
    request.setTimeout(5000, () => request.destroy(new Error('連線逾時')));
    request.on('error', reject);
  });
}
assert.equal(await status(https, port), 403);
assert.equal(await status(https, port, {'X-Forwarded-For': host}), 403);
await assert.rejects(status(http, backend), {code: 'ECONNREFUSED'});
console.log('通過：非允許網段 HTTPS 回應 403，偽造標頭無法略過限制，後端拒絕直接連線');
