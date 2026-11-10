import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { serializeUpload } from '../js/technology-upload.js';

test('the OAC checksum covers the exact binary multipart body sent to the API', async () => {
  const image = Uint8Array.from([255, 216, 255, 0, 128]);
  const form = new FormData();
  form.append('image', new Blob([image], { type: 'image/jpeg' }), 'image.jpg');
  form.append('account_id', 'a'.repeat(32));
  form.append('api_token', 'test-token-only');
  const upload = await serializeUpload(form);
  assert.equal(upload.headers['x-amz-content-sha256'], createHash('sha256').update(new Uint8Array(upload.body)).digest('hex'));
  assert.match(upload.headers['Content-Type'], /^multipart\/form-data; boundary=/);
  const received = await new Response(upload.body, { headers: upload.headers }).formData();
  assert.equal(received.get('account_id'), 'a'.repeat(32));
  assert.equal(received.get('api_token'), 'test-token-only');
  assert.deepEqual(new Uint8Array(await received.get('image').arrayBuffer()), image);
});
