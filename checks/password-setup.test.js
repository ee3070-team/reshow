import test from "node:test";
import assert from "node:assert/strict";
import { readSetupCredential, setInitialPassword } from "../js/password-setup.js";

const identity = { username: "reshow-" + "a".repeat(40), credential: "Aa1!" + "b".repeat(43) };
const config = { setup_client_id: "setupclient", issuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Test" };
const response = (body, ok = true) => ({ ok, json: async () => body });

test("setup credential is removed from browser history even for an invalid link", () => {
  const history = { replaceState: (...args) => { history.args = args; } };
  assert.deepEqual(readSetupCredential({ hash: "#" + new URLSearchParams(identity), pathname: "/auth/setup.html" }, history), identity);
  assert.deepEqual(history.args, [null, "", "/auth/setup.html"]);
  assert.throws(() => readSetupCredential({ hash: "#bad", pathname: "/auth/setup.html" }, history), /incomplete/);
});

test("password setup exchanges only the temporary credential and challenge; never returns tokens", async () => {
  const calls = [];
  const results = [config, { ChallengeName: "NEW_PASSWORD_REQUIRED", Session: "test-session" }, { AuthenticationResult: { AccessToken: "discard" } }];
  const fetcher = async (url, options) => { calls.push({ url, options }); return response(results.shift()); };
  assert.equal(await setInitialPassword(identity, "NewPassword123!", fetcher), undefined);
  assert.ok(calls.every(call => !call.url.includes(identity.credential)));
  assert.equal(JSON.parse(calls[1].options.body).AuthFlow, "USER_PASSWORD_AUTH");
  assert.equal(JSON.parse(calls[2].options.body).ChallengeResponses.NEW_PASSWORD, "NewPassword123!");
  assert.equal(JSON.parse(calls[2].options.body).Session, "test-session");
  assert.ok(calls.every(call => call.options.cache === "no-store"));
});

test("expired or used links stop before attempting a password change", async () => {
  let calls = 0;
  await assert.rejects(setInitialPassword(identity, "NewPassword123!", async () => {
    calls++;
    return calls === 1 ? response(config) : response({ __type: "NotAuthorizedException", message: identity.credential }, false);
  }), /expired/);
  assert.equal(calls, 2);
});

test("unexpected sign-in response cannot be mistaken for initial setup", async () => {
  let calls = 0;
  await assert.rejects(setInitialPassword(identity, "NewPassword123!", async () => {
    calls++;
    return response(calls === 1 ? config : { AuthenticationResult: { AccessToken: "discard" } });
  }), /no longer needs/);
  assert.equal(calls, 2);
});

test("setup never sends credentials with untrusted provider configuration", async () => {
  let calls = 0;
  await assert.rejects(setInitialPassword(identity, "NewPassword123!", async () => {
    calls++;
    return response({ ...config, issuer: "https://attacker.invalid/pool" });
  }), /not been configured/);
  assert.equal(calls, 1);
});
