// Connection check from another network namespace; no credential or terminal input is sent.
import assert from 'node:assert/strict';
import http from 'node:http';
import https from 'node:https';

const [host] = process.argv.slice(2);
assert(host, '用法：node verify-network.mjs HOST');
function status(protocol, port, path) {
  return new Promise((resolve, reject) => {
    // No secret is sent. The separate verify.py check validates TLS with the trusted CA.
    const request = protocol.get({hostname:host, port, path, rejectUnauthorized:false}, response => {
      response.resume(); resolve(response.statusCode);
    });
    request.setTimeout(5000, () => request.destroy(new Error('連線逾時')));
    request.on('error', reject);
  });
}
assert.equal(await status(https, 8443, '/'), 200);
assert.equal(await status(https, 8443, '/api/session'), 401);
await assert.rejects(status(http, 7317, '/'), {code:'ECONNREFUSED'});
console.log('通過：其他網段能開啟 HTTPS 登入頁、未授權 API 回應 401、後端無法直接連線');
