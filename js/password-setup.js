const API = "https://urauw4ckw1.execute-api.us-east-1.amazonaws.com";
const PROVIDER = "https://cognito-idp.us-east-1.amazonaws.com/";

export function readSetupCredential(location, history) {
  const fields = new URLSearchParams(location.hash.slice(1));
  // Remove the bearer credential before any network operation; never persist it.
  history.replaceState(null, "", location.pathname);
  const username = fields.get("username"), credential = fields.get("credential");
  if (!/^reshow-[a-f0-9]{40}$/.test(username || "") || !/^Aa1![A-Za-z0-9_-]{43}$/.test(credential || "")) {
    throw new Error("This setup link is incomplete. Ask your administrator for a new link.");
  }
  return { username, credential };
}

export async function setInitialPassword(identity, password, fetcher = fetch) {
  const configuration = await fetcher(API + "/auth/config", { cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", signal: AbortSignal.timeout(15000) });
  if (!configuration.ok) throw new Error("Password setup is unavailable. Please try again later.");
  const config = await configuration.json();
  if (!/^[a-zA-Z0-9]{1,128}$/.test(config.setup_client_id || "") ||
      !/^https:\/\/cognito-idp\.us-east-1\.amazonaws\.com\/us-east-1_[A-Za-z0-9]+$/.test(config.issuer || "")) {
    throw new Error("Password setup has not been configured. Contact your administrator.");
  }
  async function request(operation, body) {
    let response;
    try {
      response = await fetcher(PROVIDER, { method: "POST", cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService." + operation },
        body: JSON.stringify(body), signal: AbortSignal.timeout(15000) });
    } catch {
      throw new Error("The request did not finish. Try Live sign in with your chosen password before trying setup again.");
    }
    const result = await response.json();
    if (!response.ok) {
      const type = String(result.__type || "").split("#").pop();
      if (["InvalidPasswordException", "PasswordHistoryPolicyViolationException"].includes(type)) {
        throw new Error("Choose a different password with at least 12 characters, uppercase, lowercase, a number and a symbol.");
      }
      if (["TooManyRequestsException", "LimitExceededException"].includes(type)) throw new Error("Too many attempts. Please wait and try again.");
      throw new Error("This link may have expired, been replaced or already used. Try Live sign in, or ask your administrator for a new setup link.");
    }
    return result;
  }
  const challenge = await request("InitiateAuth", { AuthFlow: "USER_PASSWORD_AUTH", ClientId: config.setup_client_id,
    AuthParameters: { USERNAME: identity.username, PASSWORD: identity.credential } });
  if (challenge.ChallengeName !== "NEW_PASSWORD_REQUIRED" || !challenge.Session) {
    throw new Error("This account no longer needs initial password setup. Continue to Live sign in.");
  }
  const result = await request("RespondToAuthChallenge", { ClientId: config.setup_client_id,
    ChallengeName: "NEW_PASSWORD_REQUIRED", Session: challenge.Session,
    ChallengeResponses: { USERNAME: challenge.ChallengeParameters?.USER_ID_FOR_SRP || identity.username, NEW_PASSWORD: password } });
  if (!result.AuthenticationResult && !["MFA_SETUP", "SOFTWARE_TOKEN_MFA", "SMS_MFA", "EMAIL_OTP", "SELECT_MFA_TYPE"].includes(result.ChallengeName)) {
    throw new Error("Setup could not be confirmed. Try Live sign in with your chosen password.");
  }
  // Setup-client tokens are deliberately discarded; website access still requires
  // the separate browser PKCE client and the server's current account permissions.
}

if (typeof document !== "undefined") {
  const form = document.querySelector("#setup-form"), status = document.querySelector("#status");
  let identity;
  try { identity = readSetupCredential(location, history); }
  catch (error) { form.hidden = true; status.textContent = error.message; }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const password = document.querySelector("#password"), confirmation = document.querySelector("#confirmation");
    if (!identity) return;
    if (password.value !== confirmation.value) { status.textContent = "The passwords do not match."; return; }
    if (!/[a-z]/.test(password.value) || !/[A-Z]/.test(password.value) || !/[0-9]/.test(password.value) || !/[^a-zA-Z0-9\s]/.test(password.value)) {
      status.textContent = "Include uppercase, lowercase, a number and a symbol."; return;
    }
    const button = form.querySelector("button");
    button.disabled = true;
    status.textContent = "Setting your password…";
    try {
      await setInitialPassword(identity, password.value);
      identity = null;
      form.hidden = true;
      status.textContent = "Your password is set. Return to ReShow, select Live, and sign in with your email and new password.";
      document.querySelector("#sign-in").hidden = false;
    } catch (error) { status.textContent = error.message; }
    finally { password.value = confirmation.value = ""; button.disabled = false; }
  });
  addEventListener("pagehide", () => { identity = null; form.reset(); });
}
