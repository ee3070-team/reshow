// Lambda's CloudFront OAC signs these exact multipart request bytes.
export async function serializeUpload(form) {
  const upload = new Response(form);
  const body = await upload.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", body);
  const checksum = Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return {
    body,
    headers: {
      "Content-Type": upload.headers.get("Content-Type"),
      "x-amz-content-sha256": checksum,
    },
  };
}
