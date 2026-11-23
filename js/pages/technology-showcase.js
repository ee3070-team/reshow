import { confirmAction, icon } from "../ui.js";
import { serializeUpload } from "../technology-upload.js";

const THREE_URL =
  "js/vendor/three-r128/three.min.js";
const GLTF_LOADER_URL =
  "js/vendor/three-r128/GLTFLoader.js";
const FOUR_VIEW_TILE_SIZE = 480;
const FOUR_VIEWS = [
  { angle: 0, cell: [1, 0], rotation: 0 },
  { angle: 90, cell: [2, 1], rotation: Math.PI / 2 },
  { angle: 180, cell: [1, 2], rotation: Math.PI },
  { angle: 270, cell: [0, 1], rotation: -Math.PI / 2 },
];
let threeReady;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error("The 3D engine could not be loaded."));
    document.head.append(script);
  });
}

async function loadThree() {
  if (!threeReady) {
    threeReady = (async () => {
      if (!window.THREE) await loadScript(THREE_URL);
      if (!window.THREE?.GLTFLoader) await loadScript(GLTF_LOADER_URL);
      if (!window.THREE?.GLTFLoader)
        throw new Error("The 3D model loader could not be loaded.");
      return window.THREE;
    })();
  }
  return threeReady;
}

function disposeModel(THREE, model) {
  model.traverse((object) => {
    if (!object.isMesh) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    materials.forEach((material) => {
      for (const value of Object.values(material)) {
        if (value?.isTexture) value.dispose();
      }
      material.dispose();
    });
  });
}

async function loadThreeGroupModule(file, THREE) {
  if (file.size > 1024 * 1024) {
    throw new Error("Choose a Three.js Group module smaller than 1 MB.");
  }
  const source = await file.text();
  const fileUrl = URL.createObjectURL(
    new Blob([source], { type: "text/javascript" }),
  );
  try {
    const module = await import(/* webpackIgnore: true */ fileUrl);
    const factory =
      module.createModel ||
      module.createGroup ||
      module.default ||
      module.model;
    const candidate =
      typeof factory === "function"
        ? await factory(THREE)
        : factory;
    if (!candidate?.isObject3D && !candidate?.isScene) {
      throw new Error(
        "The module must export a THREE.Group, THREE.Object3D, or THREE.Scene.",
      );
    }
    return candidate;
  } finally {
    URL.revokeObjectURL(fileUrl);
  }
}

function addBunny(THREE, group) {
  const materials = {
    fur: new THREE.MeshStandardMaterial({ color: 0xffe9f0, roughness: 0.82 }),
    pink: new THREE.MeshStandardMaterial({ color: 0xff91b8, roughness: 0.72 }),
    eye: new THREE.MeshStandardMaterial({ color: 0x342536, roughness: 0.3 }),
    shine: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.25 }),
  };
  const part = (material, position, scale, rotationZ = 0) => {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), material);
    mesh.position.set(...position);
    mesh.scale.set(...scale);
    mesh.rotation.z = rotationZ;
    group.add(mesh);
  };
  part(materials.fur, [0, -0.35, 0], [0.72, 0.82, 0.55]);
  part(materials.fur, [0, 0.62, 0.02], [0.62, 0.59, 0.55]);
  part(materials.fur, [-0.31, 1.34, 0], [0.2, 0.62, 0.17], -0.16);
  part(materials.fur, [0.31, 1.34, 0], [0.2, 0.62, 0.17], 0.16);
  part(materials.pink, [-0.31, 1.36, 0.145], [0.095, 0.43, 0.045], -0.16);
  part(materials.pink, [0.31, 1.36, 0.145], [0.095, 0.43, 0.045], 0.16);
  part(materials.eye, [-0.22, 0.69, 0.51], [0.075, 0.105, 0.055]);
  part(materials.eye, [0.22, 0.69, 0.51], [0.075, 0.105, 0.055]);
  part(materials.shine, [-0.24, 0.73, 0.56], [0.024, 0.03, 0.018]);
  part(materials.shine, [0.2, 0.73, 0.56], [0.024, 0.03, 0.018]);
  part(materials.pink, [-0.4, 0.43, 0.43], [0.13, 0.075, 0.035]);
  part(materials.pink, [0.4, 0.43, 0.43], [0.13, 0.075, 0.035]);
  part(materials.pink, [0, 0.48, 0.55], [0.07, 0.05, 0.045]);
  part(materials.fur, [-0.34, -0.99, 0.23], [0.28, 0.18, 0.3]);
  part(materials.fur, [0.34, -0.99, 0.23], [0.28, 0.18, 0.3]);
  part(materials.fur, [-0.67, -0.31, 0.06], [0.2, 0.42, 0.2], -0.55);
  part(materials.fur, [0.67, -0.31, 0.06], [0.2, 0.42, 0.2], 0.55);
  part(materials.shine, [0, -0.43, -0.54], [0.22, 0.22, 0.22]);
}

export async function render(container, ctx) {
  container.innerHTML = `<div class="stack technology-studio">
    <section class="card technology-intro">
      <div>
        <h2>${icon("cube")}3D & AI studio</h2>
        <p>Prepare a model preview or turn an artifact photo into structured exhibit data.</p>
      </div>
      <span class="badge info">Local showcase tools</span>
    </section>
    <div class="segmented technology-section-selector" role="group" aria-label="Studio section">
      <button type="button" class="active" data-studio-section="model" aria-pressed="true" aria-controls="studio-model">3D model studio</button>
      <button type="button" data-studio-section="ai" aria-pressed="false" aria-controls="studio-ai">Image &amp; AI</button>
      <button type="button" data-studio-section="rfid" aria-pressed="false" aria-controls="studio-rfid">ESP32 / RFID</button>
    </div>
    <div class="technology-grid">
      <section id="studio-model" class="card technology-model" data-studio-panel="model">
        <div class="card-head"><h2>3D model studio</h2><span class="badge">Three.js</span></div>
        <div class="technology-viewer" data-viewer aria-label="Interactive 3D model preview"></div>
        <p class="technology-status" data-model-status role="status" aria-live="polite">Loading the 3D viewer…</p>
        <div class="technology-import">
          <label class="technology-field">Import a GLB or Three.js Group module
            <input data-model-file type="file" accept=".glb,.js,.mjs,model/gltf-binary,text/javascript">
          </label>
          <span class="muted small-text">Choose a .glb file or an ES module that exports a THREE.Group, THREE.Object3D, or THREE.Scene. Only import JavaScript modules you trust.</span>
        </div>
        <details class="technology-settings">
          <summary>Model settings</summary>
          <div class="technology-settings-content">
            <div class="technology-controls">
              <label>Zoom <output data-zoom-value>5.6</output>
                <input data-zoom type="range" min="3" max="10" step="0.1" value="5.6">
              </label>
              <label>Light intensity <output data-light-value>1.0</output>
                <input data-light type="range" min="0" max="2.5" step="0.1" value="1">
              </label>
            </div>
            <div class="technology-controls" aria-label="Automatic rotation speed">
              <label>Rotation speed X <output data-rotation-speed-value="x">0.000</output> rad/frame
                <input data-rotation-speed="x" type="range" min="-0.05" max="0.05" step="0.001" value="0">
              </label>
              <label>Rotation speed Y <output data-rotation-speed-value="y">0.005</output> rad/frame
                <input data-rotation-speed="y" type="range" min="-0.05" max="0.05" step="0.001" value="0.005">
              </label>
            </div>
            <p class="muted small-text">Set a speed to 0 to stop that axis, or use a negative value to reverse it.</p>
            <div class="technology-colors">
              <label>Pink light <input data-light-color="0" type="color" value="#ff69b4"></label>
              <label>Blue light <input data-light-color="1" type="color" value="#3498db"></label>
            </div>
          </div>
        </details>
        <div class="actions technology-actions">
          <button class="button" type="button" data-rotation aria-pressed="false">Pause rotation</button>
          <button class="button" type="button" data-fullscreen aria-pressed="false">Play 4-view fullscreen</button>
          <button class="button primary" type="button" data-export>Export four views</button>
        </div>
        <p class="technology-status" data-export-status role="status" aria-live="polite"></p>
      </section>
      <section id="studio-ai" class="card technology-ai" data-studio-panel="ai" hidden>
        <div class="card-head"><h2>Image &amp; AI</h2></div>
        <label class="technology-drop">
          <span>${icon("upload")}Choose a JPEG, PNG, or WebP image</span>
          <input data-image-file type="file" accept="image/jpeg,image/png,image/webp">
        </label>
        <div class="technology-image-preview" data-image-preview role="img" aria-label="Selected image preview">No image selected</div>
        <div class="segmented technology-section-selector" role="group" aria-label="Generation type">
          <button type="button" class="active" data-generation-type="json" aria-pressed="true" aria-controls="generation-json">Generate JSON</button>
          <button type="button" data-generation-type="model" aria-pressed="false" aria-controls="generation-model">Generate 3D model</button>
        </div>
        <section id="generation-json" class="technology-generation-panel" data-generation-panel="json">
          <div class="card-head"><h2>Image-to-JSON assistant</h2><span class="badge info">Workers AI</span></div>
          <p>Your image and credentials go to Cloudflare via ReShow. They are not saved.</p>
          <label class="technology-credential">Cloudflare Account ID
            <input data-account-id type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="Your 32-character account ID" required>
          </label>
          <label class="technology-credential">Cloudflare API token
            <span class="technology-token-input">
              <input data-api-token type="password" autocomplete="new-password" spellcheck="false" placeholder="Your Workers AI API token" required>
              <button class="button small" type="button" data-toggle-token aria-pressed="false">Show</button>
            </span>
          </label>
          <button class="button primary" data-generate type="button" disabled>Generate exhibit JSON</button>
          <p class="technology-status" data-ai-status role="status" aria-live="polite">Select an image to generate exhibit data.</p>
          <pre class="technology-json" data-json-output aria-live="polite">Generated exhibit JSON will appear here.</pre>
        </section>
        <section id="generation-model" class="technology-ai technology-generation-panel" data-generation-panel="model" hidden>
          <div class="card-head"><h2>Tripo 3D generation</h2><span class="badge info">Pay per task</span></div>
          <p class="technology-small">Closing this page won't cancel a task.</p>
          <label class="technology-credential">Tripo API key
            <input data-tripo-key type="password" autocomplete="off" spellcheck="false" placeholder="Enter your Tripo API key">
          </label>
          <button class="button primary" data-tripo-generate type="button">Generate textured model from image (~$0.30)</button>
          <label class="technology-field">Source image-generation task ID
            <input data-tripo-source type="text" autocomplete="off" placeholder="Filled after image generation succeeds">
          </label>
          <label class="technology-field">Describe the new texture
            <textarea data-tripo-prompt rows="3" maxlength="1024" placeholder="Worn leather with scratches"></textarea>
          </label>
          <button class="button" data-tripo-retexture type="button">Retexture with text (~$0.10)</button>
          <label class="technology-field">Tripo task ID
            <input data-tripo-task type="text" autocomplete="off" placeholder="Task ID appears after submission">
          </label>
          <button class="button small" data-tripo-check type="button">Check existing task</button>
          <p class="technology-status" data-tripo-status role="status" aria-live="polite"></p>
          <a data-tripo-download hidden target="_blank" rel="noopener noreferrer">Download GLB from Tripo</a>
        </section>
      </section>
      <section id="studio-rfid" class="card technology-ai technology-rfid" data-studio-panel="rfid" hidden>
        <div class="card-head"><h2>ESP32/RFID workflow</h2><span class="badge info">Hardware</span></div>
        <details class="technology-settings rfid-help">
          <summary>Card roles and workflow help</summary>
          <div class="technology-settings-content">
            <dl class="rfid-phase-guide">
              <div><dt>Door card</dt><dd>Opens the door for about 3 seconds. When idle, it also starts a manual exhibit update.</dd></div>
              <div><dt>E-paper card</dt><dd>Does not open the door. It requests the saved description for the e-paper display and, when idle, starts a manual exhibit update.</dd></div>
              <div><dt>idle</dt><dd>No manual update is in progress. Scan a door or e-paper card to begin.</dd></div>
              <div><dt>pending_edit</dt><dd>A card was accepted. Select an exhibit and save its description to finish.</dd></div>
            </dl>
            <p class="technology-status">After saving a description, the ESP32 checks the server and updates the e-paper automatically. No second tap is needed.</p>
          </div>
        </details>
        <p class="technology-status" data-rfid-delivery role="status" aria-live="polite">Checking e-paper delivery…</p>
        <pre class="technology-json" data-rfid-status aria-live="polite">Loading RFID workflow…</pre>
        <label class="technology-credential">Exhibit ID
          <output data-rfid-exhibit>Waiting for ESP32 scan</output>
        </label>
        <form data-rfid-edit>
          <label class="technology-field">Exhibit to edit
            <select data-rfid-edit-exhibit required disabled><option value="">Scan a card first</option></select>
          </label>
          <label class="technology-field">Description for the exhibit and e-paper
            <textarea data-rfid-description rows="5" maxlength="180" required disabled></textarea>
          </label>
          <p class="technology-small">Use 1–180 printable English characters. Select the exhibit explicitly: the hardware ID may not match the website ID.</p>
          <button class="button primary" type="submit" data-rfid-save disabled>Save description and finish</button>
          <p class="technology-status" data-rfid-edit-message role="status" aria-live="polite"></p>
        </form>
        <p class="technology-status" data-rfid-message role="status" aria-live="polite">Waiting for the ESP32 workflow server.</p>
        <details class="technology-settings">
          <summary>RFID event example</summary>
          <div class="technology-settings-content">
            <p class="technology-status">Send a demo event. The latest event and e-paper status appear in the workflow status.</p>
            <div class="actions">
              <button class="button" type="button" data-rfid-example-post>Send example RFID event</button>
            </div>
          </div>
        </details>
        <details class="technology-settings">
          <summary>Test card roles</summary>
          <div class="technology-settings-content">
            <p class="technology-status">These simulated taps change the server's RFID workflow but do not operate the door or e-paper. Use only when no real workflow is in progress. Select an exhibit and save its description to finish.</p>
            <div class="actions">
              <button class="button" type="button" data-rfid-test-role="door">Test door card</button>
              <button class="button" type="button" data-rfid-test-role="epaper">Test e-paper card</button>
            </div>
          </div>
        </details>
        <pre class="technology-json" data-rfid-event aria-live="polite">No RFID event loaded.</pre>
        <p class="technology-status" data-rfid-event-message role="status" aria-live="polite"></p>
      </section>
    </div>
  </div>`;

  const controller = new AbortController();
  const { signal } = controller;
  const sectionButtons = container.querySelectorAll("[data-studio-section]");
  const sectionPanels = container.querySelectorAll("[data-studio-panel]");
  sectionButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const selected = button.dataset.studioSection;
      sectionButtons.forEach((item) => {
        const isSelected = item === button;
        item.classList.toggle("active", isSelected);
        item.setAttribute("aria-pressed", String(isSelected));
      });
      sectionPanels.forEach((panel) => {
        panel.hidden = panel.dataset.studioPanel !== selected;
      });
    }, { signal });
  });
  const generationButtons = container.querySelectorAll("[data-generation-type]");
  const generationPanels = container.querySelectorAll("[data-generation-panel]");
  generationButtons.forEach((button) => {
    button.addEventListener("click", () => {
      generationButtons.forEach((item) => {
        const isSelected = item === button;
        item.classList.toggle("active", isSelected);
        item.setAttribute("aria-pressed", String(isSelected));
      });
      generationPanels.forEach((panel) => {
        panel.hidden = panel.dataset.generationPanel !== button.dataset.generationType;
      });
    }, { signal });
  });
  const viewer = container.querySelector("[data-viewer]");
  const modelStatus = container.querySelector("[data-model-status]");
  const exportStatus = container.querySelector("[data-export-status]");
  const zoom = container.querySelector("[data-zoom]");
  const zoomValue = container.querySelector("[data-zoom-value]");
  const intensity = container.querySelector("[data-light]");
  const intensityValue = container.querySelector("[data-light-value]");
  const rotationButton = container.querySelector("[data-rotation]");
  const rotationControls = Array.from(container.querySelectorAll("[data-rotation-speed]"), (input) => ({
    input,
    output: container.querySelector(`[data-rotation-speed-value="${input.dataset.rotationSpeed}"]`),
  }));
  const rotationSpeedX = container.querySelector('[data-rotation-speed="x"]');
  const rotationSpeedY = container.querySelector('[data-rotation-speed="y"]');
  const fullscreenButton = container.querySelector("[data-fullscreen]");
  let THREE;
  let renderer;
  let camera;
  let model;
  let modelContent;
  let lights = [];
  let ambient;
  let autoRotate = true;
  let cameraAngle = 0;
  let cameraDistance = 5.6;
  let targetY = 0.35;
  let animationFrame;
  let active = true;
  let dragging = false;
  let previousPointer = null;
  let fourViewCanvas = null;
  let fourViewContext = null;
  let fullscreenExitButton = null;
  let loadedImage = null;
  let selectedImage = null;

  const updateCamera = () => {
    if (!camera || !THREE) return;
    const radians = THREE.MathUtils.degToRad(cameraAngle);
    camera.position.set(
      cameraDistance * Math.sin(radians),
      targetY,
      cameraDistance * Math.cos(radians),
    );
    camera.up.set(0, 1, 0);
    camera.lookAt(0, targetY, 0);
  };

  const setAutoRotate = (enabled) => {
    autoRotate = enabled;
    rotationButton.textContent = enabled ? "Pause rotation" : "Resume rotation";
    rotationButton.setAttribute("aria-pressed", String(!enabled));
  };
  try {
    THREE = await loadThree();
    if (!active) return;
    const scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(
      45,
      Math.max(1, viewer.clientWidth) / Math.max(1, viewer.clientHeight),
      0.1,
      1000,
    );
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(Math.max(1, viewer.clientWidth), Math.max(1, viewer.clientHeight));
    viewer.replaceChildren(renderer.domElement);
    scene.add(camera);
    const pink = new THREE.DirectionalLight(0xff69b4, 1);
    pink.position.set(-1.2, 1, 1);
    pink.target.position.set(0, 0, -1);
    camera.add(pink, pink.target);
    const blue = new THREE.DirectionalLight(0x3498db, 1);
    blue.position.set(1.2, -0.5, 1);
    blue.target.position.set(0, 0, -1);
    camera.add(blue, blue.target);
    lights = [pink, blue];
    ambient = new THREE.AmbientLight(0x222222, 1);
    scene.add(ambient);
    model = new THREE.Group();
    modelContent = new THREE.Group();
    model.add(modelContent);
    addBunny(THREE, modelContent);
    scene.add(model);
    updateCamera();
    modelStatus.textContent = "Drag the model to rotate it; use the wheel to zoom.";

    const resizeRenderer = () => {
      if (!active || !renderer) return;
      if (fourViewCanvas) {
        camera.aspect = 1;
        camera.updateProjectionMatrix();
        renderer.setSize(FOUR_VIEW_TILE_SIZE, FOUR_VIEW_TILE_SIZE, false);
        return;
      }
      const width = Math.max(1, viewer.clientWidth);
      const height = Math.max(1, viewer.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    };
    const resize = new ResizeObserver(() => {
      resizeRenderer();
    });
    resize.observe(viewer);

    const isFourViewMode = () =>
      document.fullscreenElement === viewer ||
      viewer.classList.contains("is-pseudo-fullscreen");
    const leaveFourView = () => {
      if (viewer.classList.contains("is-pseudo-fullscreen")) {
        viewer.classList.remove("is-pseudo-fullscreen");
        syncFullscreen();
        return;
      }
      document.exitFullscreen().catch((error) => {
        exportStatus.textContent =
          error instanceof Error && error.message
            ? `Could not exit fullscreen: ${error.message}`
            : "Could not exit fullscreen. Press Escape to close it.";
      });
    };
    const syncFullscreen = () => {
      const isFullscreen = isFourViewMode();
      if (isFullscreen && !fourViewCanvas) {
        fourViewCanvas = document.createElement("canvas");
        fourViewCanvas.className = "technology-four-view";
        fourViewCanvas.setAttribute(
          "aria-label",
          "Four-view fullscreen model playback",
        );
        fourViewContext = fourViewCanvas.getContext("2d");
        if (!fourViewContext) {
          fourViewCanvas = null;
          exportStatus.textContent =
            "Could not create the fullscreen four-view display.";
          leaveFourView();
          return;
        }
        fourViewCanvas.width = FOUR_VIEW_TILE_SIZE * 3;
        fourViewCanvas.height = FOUR_VIEW_TILE_SIZE * 3;
        fullscreenExitButton = document.createElement("button");
        fullscreenExitButton.type = "button";
        fullscreenExitButton.className = "technology-fullscreen-exit";
        fullscreenExitButton.textContent = "Exit 4-view";
        fullscreenExitButton.setAttribute("aria-label", "Exit fullscreen 4-view");
        fullscreenExitButton.addEventListener(
          "click",
          () => leaveFourView(),
          { signal },
        );
        viewer.append(fourViewCanvas, fullscreenExitButton);
        renderer.domElement.hidden = true;
        viewer.classList.add("is-four-view");
        fullscreenButton.setAttribute("aria-pressed", "true");
        exportStatus.textContent =
          "Four-view fullscreen playback is active. Press Escape to exit.";
      } else if (!isFullscreen && fourViewCanvas) {
        fourViewCanvas.remove();
        fullscreenExitButton.remove();
        fourViewCanvas = null;
        fourViewContext = null;
        fullscreenExitButton = null;
        renderer.domElement.hidden = false;
        viewer.classList.remove("is-four-view");
        fullscreenButton.setAttribute("aria-pressed", "false");
        exportStatus.textContent = "Four-view fullscreen playback closed.";
      }
      resizeRenderer();
    };
    document.addEventListener("fullscreenchange", syncFullscreen, { signal });
    const playFourViewInPage = () => {
      // The Fullscreen API can be refused (for example inside an embedded
      // preview). Show the same four views as an in-page overlay instead.
      viewer.classList.add("is-pseudo-fullscreen");
      syncFullscreen();
      exportStatus.textContent =
        "The browser blocked fullscreen, so the four views are shown inside the page. Click Exit 4-view or press Escape to close them.";
    };
    document.addEventListener(
      "keydown",
      (event) => {
        if (event.key === "Escape" && viewer.classList.contains("is-pseudo-fullscreen")) {
          leaveFourView();
        }
      },
      { signal },
    );
    fullscreenButton.addEventListener(
      "click",
      async () => {
        if (!renderer || !camera || !model) {
          exportStatus.textContent = "The 3D viewer is not ready yet.";
          return;
        }
        exportStatus.textContent = "Opening four-view playback…";
        if (!viewer.requestFullscreen) {
          playFourViewInPage();
          return;
        }
        try {
          await Promise.race([
            viewer.requestFullscreen(),
            new Promise((resolve) => window.setTimeout(resolve, 900)),
          ]);
        } catch (error) {
          playFourViewInPage();
          exportStatus.textContent =
            "The browser blocked fullscreen (" +
            (error instanceof Error && error.message
              ? error.message
              : "unknown reason") +
            "), so the four views are shown inside the page. Click Exit 4-view or press Escape to close them.";
          return;
        }
        if (!isFourViewMode()) {
          // Embedded previews may swallow the request without reporting an
          // error, so fall back whenever fullscreen did not actually start.
          playFourViewInPage();
        }
      },
      { signal },
    );

    const renderFrame = () => {
      if (!active) return;
      if (autoRotate && !dragging) {
        model.rotation.y += Number(rotationSpeedY.value);
        model.rotation.x += Number(rotationSpeedX.value);
      }
      if (fourViewCanvas) {
        const oldAngle = cameraAngle;
        fourViewContext.fillStyle = "#000";
        fourViewContext.fillRect(
          0,
          0,
          fourViewCanvas.width,
          fourViewCanvas.height,
        );
        for (const view of FOUR_VIEWS) {
          cameraAngle = view.angle;
          updateCamera();
          renderer.render(scene, camera);
          fourViewContext.save();
          fourViewContext.translate(
            (view.cell[0] + 0.5) * FOUR_VIEW_TILE_SIZE,
            (view.cell[1] + 0.5) * FOUR_VIEW_TILE_SIZE,
          );
          fourViewContext.rotate(view.rotation);
          fourViewContext.drawImage(
            renderer.domElement,
            -FOUR_VIEW_TILE_SIZE / 2,
            -FOUR_VIEW_TILE_SIZE / 2,
            FOUR_VIEW_TILE_SIZE,
            FOUR_VIEW_TILE_SIZE,
          );
          fourViewContext.restore();
        }
        cameraAngle = oldAngle;
        updateCamera();
      } else {
        renderer.render(scene, camera);
      }
      animationFrame = requestAnimationFrame(renderFrame);
    };
    renderFrame();

    viewer.addEventListener(
      "pointerdown",
      (event) => {
        if (event.target.closest(".technology-fullscreen-exit")) return;
        dragging = true;
        previousPointer = { x: event.clientX, y: event.clientY };
        viewer.setPointerCapture(event.pointerId);
      },
      { signal },
    );
    viewer.addEventListener(
      "pointermove",
      (event) => {
        if (!dragging || !previousPointer) return;
        model.rotation.y += (event.clientX - previousPointer.x) * 0.01;
        model.rotation.x += (event.clientY - previousPointer.y) * 0.01;
        previousPointer = { x: event.clientX, y: event.clientY };
      },
      { signal },
    );
    viewer.addEventListener(
      "pointerup",
      () => {
        dragging = false;
        previousPointer = null;
      },
      { signal },
    );
    viewer.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        cameraDistance = Math.min(
          10,
          Math.max(3, cameraDistance + Math.sign(event.deltaY) * 0.35),
        );
        zoom.value = String(cameraDistance);
        zoomValue.value = cameraDistance.toFixed(1);
        updateCamera();
      },
      { passive: false, signal },
    );
    zoom.addEventListener(
      "input",
      () => {
        cameraDistance = Number(zoom.value);
        zoomValue.value = cameraDistance.toFixed(1);
        updateCamera();
      },
      { signal },
    );
    intensity.addEventListener(
      "input",
      () => {
        lights.forEach((light) => (light.intensity = Number(intensity.value)));
        ambient.intensity = Number(intensity.value);
        intensityValue.value = Number(intensity.value).toFixed(1);
      },
      { signal },
    );
    container.querySelectorAll("[data-light-color]").forEach((input) => {
      input.addEventListener(
        "input",
        () => {
          lights[Number(input.dataset.lightColor)].color.set(input.value);
        },
        { signal },
      );
    });
    rotationControls.forEach(({ input, output }) => {
      input.addEventListener("input", () => {
        output.textContent = Number(input.value).toFixed(3);
      }, { signal });
    });
    rotationButton.addEventListener(
      "click",
      () => setAutoRotate(!autoRotate),
      { signal },
    );
    container.querySelector("[data-model-file]").addEventListener(
      "change",
      async (event) => {
        const file = event.currentTarget.files?.[0];
        event.currentTarget.value = "";
        if (!file) return;
        const fileName = file.name.toLowerCase();
        const groupModule = fileName.endsWith(".js") || fileName.endsWith(".mjs");
        if (!fileName.endsWith(".glb") && !groupModule) {
          modelStatus.textContent = "Choose a .glb file or a .js/.mjs Three.js Group module.";
          return;
        }
        if (!THREE.GLTFLoader) {
          modelStatus.textContent = "The 3D model loader is unavailable.";
          return;
        }
        modelStatus.textContent = `Loading ${file.name}…`;
        if (groupModule) {
          try {
            const imported = await loadThreeGroupModule(file, THREE);
            imported.updateMatrixWorld(true);
            const bounds = new THREE.Box3().setFromObject(imported);
            const size = bounds.getSize(new THREE.Vector3());
            const largest = Math.max(size.x, size.y, size.z);
            if (!Number.isFinite(largest) || largest <= 0) {
              disposeModel(THREE, imported);
              modelStatus.textContent = "This model has no displayable geometry.";
              return;
            }
            imported.scale.multiplyScalar(2.8 / largest);
            imported.updateMatrixWorld(true);
            imported.position.sub(
              new THREE.Box3()
                .setFromObject(imported)
                .getCenter(new THREE.Vector3()),
            );
            model.remove(modelContent);
            disposeModel(THREE, modelContent);
            modelContent = imported;
            model.add(modelContent);
            model.rotation.set(0, 0, 0);
            targetY = 0;
            cameraAngle = 0;
            updateCamera();
            modelStatus.textContent =
              `Loaded ${file.name}. The model has been centered and resized.`;
          } catch (error) {
            modelStatus.textContent =
              error instanceof Error && error.message
                ? error.message
                : "This Three.js Group module could not be loaded.";
          }
          return;
        }
        const url = URL.createObjectURL(file);
        new THREE.GLTFLoader().load(
          url,
          (gltf) => {
            URL.revokeObjectURL(url);
            if (!active) {
              disposeModel(THREE, gltf.scene);
              return;
            }
            const imported = gltf.scene;
            imported.updateMatrixWorld(true);
            const bounds = new THREE.Box3().setFromObject(imported);
            const size = bounds.getSize(new THREE.Vector3());
            const largest = Math.max(size.x, size.y, size.z);
            if (!Number.isFinite(largest) || largest <= 0) {
              disposeModel(THREE, imported);
              modelStatus.textContent = "This model has no displayable geometry.";
              return;
            }
            imported.scale.multiplyScalar(2.8 / largest);
            imported.updateMatrixWorld(true);
            imported.position.sub(
              new THREE.Box3()
                .setFromObject(imported)
                .getCenter(new THREE.Vector3()),
            );
            model.remove(modelContent);
            disposeModel(THREE, modelContent);
            modelContent = imported;
            model.add(modelContent);
            model.rotation.set(0, 0, 0);
            targetY = 0;
            cameraAngle = 0;
            updateCamera();
            modelStatus.textContent = `Loaded ${file.name}. The model has been centered and resized.`;
          },
          undefined,
          () => {
            URL.revokeObjectURL(url);
            if (active)
              modelStatus.textContent =
                "Model loading failed. Check that the file is a valid GLB.";
          },
        );
      },
      { signal },
    );
    container.querySelector("[data-export]").addEventListener(
      "click",
      () => {
        if (!renderer || !camera || !model) {
          exportStatus.textContent = "The 3D viewer is not ready yet.";
          return;
        }
        const size = FOUR_VIEW_TILE_SIZE;
        const output = document.createElement("canvas");
        output.width = size * 3;
        output.height = size * 3;
        const context = output.getContext("2d");
        if (!context) {
          exportStatus.textContent = "Could not create an image for export.";
          return;
        }
        const oldRotation = model.rotation.clone();
        const oldAngle = cameraAngle;
        const oldAspect = camera.aspect;
        const oldSize = renderer.getSize(new THREE.Vector2());
        try {
          context.fillStyle = "#000";
          context.fillRect(0, 0, output.width, output.height);
          model.rotation.set(0, 0, 0);
          renderer.setSize(size, size, false);
          camera.aspect = 1;
          camera.updateProjectionMatrix();
          for (const view of FOUR_VIEWS) {
            cameraAngle = view.angle;
            updateCamera();
            renderer.render(model.parent, camera);
            context.save();
            context.translate(
              (view.cell[0] + 0.5) * size,
              (view.cell[1] + 0.5) * size,
            );
            context.rotate(view.rotation);
            context.drawImage(
              renderer.domElement,
              -size / 2,
              -size / 2,
              size,
              size,
            );
            context.restore();
          }
        } catch (error) {
          console.error("Could not export the four model views.", error);
          exportStatus.textContent = "Export failed. Please try again.";
          return;
        } finally {
          model.rotation.copy(oldRotation);
          cameraAngle = oldAngle;
          camera.aspect = oldAspect;
          camera.updateProjectionMatrix();
          renderer.setSize(oldSize.x, oldSize.y, false);
          updateCamera();
        }
        output.toBlob((blob) => {
          if (!blob) {
            exportStatus.textContent = "Image export failed. Please try again.";
            return;
          }
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = "reshow-hologram-4view.png";
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          exportStatus.textContent = "Four-view hologram downloaded.";
        }, "image/png");
      },
      { signal },
    );
    signal.addEventListener("abort", () => resize.disconnect(), { once: true });
  } catch (error) {
    if (active) {
      viewer.textContent = "The 3D engine could not be loaded. Check your internet connection and refresh this tab.";
      modelStatus.textContent = error.message;
    }
  }

  const imageInput = container.querySelector("[data-image-file]");
  const generateButton = container.querySelector("[data-generate]");
  const aiStatus = container.querySelector("[data-ai-status]");
  const preview = container.querySelector("[data-image-preview]");
  const jsonOutput = container.querySelector("[data-json-output]");
  const accountIdInput = container.querySelector("[data-account-id]");
  const apiTokenInput = container.querySelector("[data-api-token]");
  const tripoKey = container.querySelector("[data-tripo-key]");
  const tripoSource = container.querySelector("[data-tripo-source]");
  const tripoPrompt = container.querySelector("[data-tripo-prompt]");
  const tripoGenerate = container.querySelector("[data-tripo-generate]");
  const tripoRetexture = container.querySelector("[data-tripo-retexture]");
  const tripoTask = container.querySelector("[data-tripo-task]");
  const tripoCheck = container.querySelector("[data-tripo-check]");
  const tripoStatus = container.querySelector("[data-tripo-status]");
  const tripoDownload = container.querySelector("[data-tripo-download]");
  const tripoRequest = async (payload) => {
    const body = JSON.stringify(payload);
    const bytes = new TextEncoder().encode(body);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const checksum = Array.from(new Uint8Array(hash), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    const response = await fetch("/api/technology/generate-exhibit", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-amz-content-sha256": checksum },
      body: bytes,
      signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
    if (!result.data || typeof result.data !== "object") throw new Error("Invalid Tripo response.");
    return result.data;
  };
  const loadTripoModel = (url) => new Promise((resolve, reject) => {
    if (!THREE?.GLTFLoader || !model || !modelContent) {
      reject(new Error("The 3D viewer is not ready."));
      return;
    }
    let finished = false;
    const timer = setTimeout(() => {
      finished = true;
      reject(new Error("Loading the GLB timed out. Download it and use Import a GLB."));
    }, 30000);
    new THREE.GLTFLoader().load(url, (gltf) => {
      const imported = gltf.scene;
      if (finished || !active) {
        disposeModel(THREE, imported);
        if (!finished) {
          finished = true;
          clearTimeout(timer);
          resolve();
        }
        return;
      }
      finished = true;
      clearTimeout(timer);
      imported.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(imported);
      const size = bounds.getSize(new THREE.Vector3());
      const largest = Math.max(size.x, size.y, size.z);
      if (!Number.isFinite(largest) || largest <= 0) {
        disposeModel(THREE, imported);
        reject(new Error("The model has no displayable geometry."));
        return;
      }
      imported.scale.multiplyScalar(2.8 / largest);
      imported.updateMatrixWorld(true);
      imported.position.sub(new THREE.Box3().setFromObject(imported).getCenter(new THREE.Vector3()));
      model.remove(modelContent);
      disposeModel(THREE, modelContent);
      modelContent = imported;
      model.add(modelContent);
      model.rotation.set(0, 0, 0);
      targetY = 0;
      cameraAngle = 0;
      updateCamera();
      modelStatus.textContent = "Tripo model loaded and centered.";
      resolve();
    }, undefined, () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      reject(new Error("The browser could not load the GLB directly. Download it and use Import a GLB."));
    });
  });
  const pollTripo = async (key, taskId, action) => {
    const deadline = Date.now() + 5 * 60 * 1000;
    while (Date.now() < deadline) {
      const result = await tripoRequest({ action: "status", api_key: key, task_id: taskId });
      if (result.status === "success") {
        if (action === "image" || result.type === "image_to_model") {
          tripoSource.value = taskId;
        }
        const url = new URL(result.model_url);
        if (url.protocol !== "https:" || url.username || url.password)
          throw new Error("Tripo returned an invalid model URL.");
        tripoDownload.href = url.href;
        tripoDownload.hidden = false;
        const cost = typeof result.credits_consumed === "number"
          ? ` (${result.credits_consumed} credits used)` : "";
        tripoStatus.textContent = `Task complete${cost}. Loading the GLB…`;
        try {
          await loadTripoModel(url.href);
          if (active) tripoStatus.textContent = `Task complete${cost}. Open 3D model studio to view it. Download the GLB soon; the link expires.`;
        } catch (error) {
          if (active) tripoStatus.textContent = `Task complete${cost}. ${error.message} The download link expires soon.`;
        }
        return;
      }
      if (["failed", "cancelled", "banned", "expired"].includes(result.status))
        throw new Error(result.error || `Tripo task ${result.status}.`);
      tripoStatus.textContent = `Task ${taskId}: ${result.status}${Number.isFinite(result.progress) ? ` (${result.progress}%)` : ""}.`;
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (!active) return;
    }
    throw new Error("Polling timed out. Use Check existing task to resume; do not generate again unless intended.");
  };
  const runTripo = async (action) => {
    const key = tripoKey.value.trim();
    if (!(window.isSecureContext || ["localhost", "127.0.0.1", "::1"].includes(location.hostname))) {
      tripoStatus.textContent = "Use HTTPS or localhost before entering your API key.";
      return;
    }
    if (!key) {
      tripoStatus.textContent = "Enter your Tripo API key.";
      return;
    }
    if (action === "status" && !tripoTask.value.trim()) {
      tripoStatus.textContent = "Enter an existing Tripo task ID.";
      return;
    }
    if (action === "retexture" && !tripoSource.value.trim()) {
      tripoStatus.textContent = "Enter the source image-generation task ID first.";
      return;
    }
    if (action === "retexture" && !tripoPrompt.value.trim()) {
      tripoStatus.textContent = "Describe the new texture first.";
      return;
    }
    if (action === "image" && !loadedImage) {
      tripoStatus.textContent = "Choose an image above first.";
      return;
    }
    tripoGenerate.disabled = true;
    tripoRetexture.disabled = true;
    tripoCheck.disabled = true;
    tripoDownload.hidden = true;
    try {
      let taskId = tripoTask.value.trim();
      if (action !== "status") {
        const payload = { action, api_key: key };
        if (action === "retexture") {
          payload.source_task_id = tripoSource.value.trim();
          payload.prompt = tripoPrompt.value.trim();
        } else {
          const scale = Math.min(1, 1024 / Math.max(loadedImage.width, loadedImage.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(loadedImage.width * scale));
          canvas.height = Math.max(1, Math.round(loadedImage.height * scale));
          const context = canvas.getContext("2d");
          if (!context) throw new Error("Could not prepare the image.");
          context.drawImage(loadedImage, 0, 0, canvas.width, canvas.height);
          payload.image = canvas.toDataURL("image/jpeg", 0.8).split(",")[1];
        }
        tripoStatus.textContent = "Submitting a paid Tripo task…";
        tripoTask.value = "";
        const created = await tripoRequest(payload);
        if (typeof created.task_id !== "string" || !created.task_id)
          throw new Error("No task ID returned. Check Tripo task history before retrying.");
        taskId = created.task_id;
        tripoTask.value = taskId;
      }
      await pollTripo(key, taskId, action);
    } catch (error) {
      if (error.name !== "AbortError") {
        tripoStatus.textContent = `${error.message} ${tripoTask.value ? "Use the task ID to check again." : "Check Tripo task history before retrying if submission started."}`;
      }
    } finally {
      if (active) {
        tripoGenerate.disabled = false;
        tripoRetexture.disabled = false;
        tripoCheck.disabled = false;
      }
    }
  };
  tripoGenerate.addEventListener("click", () => runTripo("image"), { signal });
  tripoRetexture.addEventListener("click", () => runTripo("retexture"), { signal });
  tripoCheck.addEventListener("click", () => runTripo("status"), { signal });
  const loadImageFile = (file) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      if (!active) return;
      loadedImage = image;
      preview.replaceChildren(image);
      image.alt = "Selected artifact image preview";
      generateButton.disabled = false;
      aiStatus.textContent = "Image ready. Generate exhibit JSON when ready.";
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      preview.replaceChildren();
      preview.textContent = "The selected image could not be decoded.";
      selectedImage = null;
      loadedImage = null;
      generateButton.disabled = true;
      aiStatus.textContent = "The selected image could not be decoded.";
    };
    image.src = url;
  };
  container.querySelector("[data-toggle-token]").addEventListener(
    "click",
    (event) => {
      const showing = apiTokenInput.type === "password";
      apiTokenInput.type = showing ? "text" : "password";
      event.currentTarget.textContent = showing ? "Hide" : "Show";
      event.currentTarget.setAttribute("aria-pressed", String(showing));
    },
    { signal },
  );
  imageInput.addEventListener(
    "change",
    () => {
      selectedImage = imageInput.files?.[0] || null;
      loadedImage = null;
      generateButton.disabled = true;
      jsonOutput.textContent = "Generated exhibit JSON will appear here.";
      if (!selectedImage) {
        preview.replaceChildren();
        preview.textContent = "No image selected";
        aiStatus.textContent = "Select an image to generate exhibit data.";
        return;
      }
      if (
        !["image/jpeg", "image/png", "image/webp"].includes(selectedImage.type)
      ) {
        selectedImage = null;
        preview.replaceChildren();
        preview.textContent = "No image selected";
        aiStatus.textContent = "Choose a JPEG, PNG, or WebP image.";
        return;
      }
      loadImageFile(selectedImage);
    },
    { signal },
  );
  generateButton.addEventListener(
    "click",
    async () => {
      if (!loadedImage || !selectedImage) {
        aiStatus.textContent = "Choose an image before generating exhibit data.";
        return;
      }
      const accountId = accountIdInput.value.trim();
      const apiToken = apiTokenInput.value.trim();
      if (!/^[a-f\d]{32}$/i.test(accountId)) {
        aiStatus.textContent = "Enter a valid 32-character Cloudflare Account ID.";
        accountIdInput.focus();
        return;
      }
      if (!apiToken) {
        aiStatus.textContent = "Enter your Cloudflare API token.";
        apiTokenInput.focus();
        return;
      }
      generateButton.disabled = true;
      aiStatus.textContent = "Generating exhibit JSON from the image…";
      try {
        const maxDimension = 1024;
        const scale = Math.min(
          1,
          maxDimension / Math.max(loadedImage.width, loadedImage.height),
        );
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(loadedImage.width * scale));
        canvas.height = Math.max(1, Math.round(loadedImage.height * scale));
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Could not prepare the image.");
        context.drawImage(loadedImage, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", 0.8),
        );
        if (!blob) throw new Error("Could not prepare the image.");
        const data = new FormData();
        data.append("image", blob, "image.jpg");
        data.append("account_id", accountId);
        data.append("api_token", apiToken);
        const upload = await serializeUpload(data);
        const response = await fetch("/api/technology/generate-exhibit", {
          method: "POST",
          ...upload,
          signal,
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok)
          throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
        if (
          !result.data ||
          typeof result.data !== "object" ||
          Array.isArray(result.data)
        )
          throw new Error("The server returned no exhibit JSON.");
        jsonOutput.textContent = JSON.stringify(result.data, null, 2);

        aiStatus.textContent = "Exhibit JSON generated from the image.";
      } catch (error) {
        if (error.name !== "AbortError") {
          console.error("Image-to-JSON generation failed.", error);
          aiStatus.textContent = error instanceof Error ? error.message : String(error);
        }
      } finally {
        if (active) generateButton.disabled = !loadedImage;
      }
    },
    { signal },
  );

  const rfidStatus = container.querySelector("[data-rfid-status]");
  const rfidMessage = container.querySelector("[data-rfid-message]");
  const rfidDelivery = container.querySelector("[data-rfid-delivery]");
  const rfidExhibit = container.querySelector("[data-rfid-exhibit]");
  const rfidExamplePost = container.querySelector("[data-rfid-example-post]");
  const rfidEvent = container.querySelector("[data-rfid-event]");
  const rfidEventMessage = container.querySelector("[data-rfid-event-message]");
  const rfidTestButtons = container.querySelectorAll("[data-rfid-test-role]");
  const rfidForm = container.querySelector("[data-rfid-edit]");
  const rfidSelect = container.querySelector("[data-rfid-edit-exhibit]");
  const rfidDescription = container.querySelector("[data-rfid-description]");
  const rfidSave = container.querySelector("[data-rfid-save]");
  const rfidEditMessage = container.querySelector("[data-rfid-edit-message]");
  let rfidEditId = null;
  let rfidProfiles = [];
  let rfidSaving = false;
  let rfidDirty = false;
  let rfidSelectedId = "";
  let rfidRefreshSequence = 0;
  let rfidPolling;

  const syncRfidEditor = async (workflow) => {
    if (rfidSaving) return;
    if (workflow.phase === "pending_edit" && !workflow.edit_id) {
      rfidEditId = null;
      rfidSelect.disabled = true;
      rfidDescription.disabled = true;
      rfidSave.disabled = true;
      rfidEditMessage.textContent = "The local server needs to be restarted before you can select an exhibit.";
      return;
    }
    const editId = workflow.phase === "pending_edit" ? workflow.edit_id : null;
    if (editId === rfidEditId) return;
    rfidEditId = editId;
    rfidSelect.disabled = true;
    rfidDescription.disabled = true;
    rfidSave.disabled = true;
    if (rfidDirty) {
      rfidEditMessage.textContent = "The RFID session changed. Unsaved text was kept; copy it before starting another edit.";
      return;
    }
    rfidSelect.replaceChildren(new Option(editId ? "Select an exhibit" : "Scan a card first", ""));
    rfidSelectedId = "";
    rfidDescription.value = "";
    if (!editId) return;
    try {
      const profiles = await ctx.api.listExhibits();
      if (!active || rfidEditId !== editId) return;
      rfidProfiles = profiles;
      profiles.forEach((profile) => rfidSelect.add(new Option(`${profile.name} (${profile.id})`, profile.id)));
      rfidSelect.disabled = false;
      rfidDescription.disabled = false;
      rfidSave.disabled = false;
      rfidEditMessage.textContent = profiles.length ? "Select the exhibit to edit." : "No exhibits are available.";
    } catch (error) {
      rfidEditId = null;
      rfidEditMessage.textContent = `Could not load exhibits: ${error.message}`;
    }
  };

  const refreshRfidStatus = async () => {
    const refreshSequence = ++rfidRefreshSequence;
    try {
      const response = await fetch("/api/rfid/status", { signal });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
      const workflow = result.data || {};
      if (refreshSequence !== rfidRefreshSequence) return;
      rfidStatus.textContent = JSON.stringify(workflow, null, 2);
      rfidExhibit.textContent = workflow.exhibit_id || "Waiting for ESP32 scan";
      rfidMessage.textContent = "ESP32 workflow status is available.";
      const deliveryMessages = {
        not_saved: "No e-paper description has been saved yet.",
        pending: "Description saved; waiting for the ESP32 and Mega to confirm the display update.",
        confirmed: "The Mega last confirmed this e-paper update.",
      };
      if (!Object.prototype.hasOwnProperty.call(deliveryMessages, workflow.epaper_delivery)) {
        throw new Error("Invalid e-paper delivery status.");
      }
      rfidDelivery.textContent = deliveryMessages[workflow.epaper_delivery];
      if (workflow.latest_event) {
        rfidEvent.textContent = JSON.stringify(workflow.latest_event, null, 2);
        rfidEventMessage.textContent =
          `Card tap received: ${workflow.latest_event.uid} (${workflow.latest_event.result}).`;
      } else {
        rfidEvent.textContent = "No RFID event loaded.";
        rfidEventMessage.textContent = "Waiting for a card tap.";
      }
      await syncRfidEditor(workflow);
    } catch (error) {
      if (error.name === "AbortError") return;
      if (refreshSequence !== rfidRefreshSequence) return;
      rfidStatus.textContent = '{"phase":"unavailable"}';
      rfidMessage.textContent = error.message;
      rfidDelivery.textContent = `E-paper delivery status unavailable: ${error.message}`;
    }
  };

  rfidSelect.addEventListener("change", async () => {
    const nextId = rfidSelect.value;
    if (rfidDirty) {
      const editId = rfidEditId;
      rfidSelect.disabled = true;
      rfidDescription.disabled = true;
      rfidSave.disabled = true;
      try {
        const discard = await confirmAction(
          "Discard unsaved description?",
          "Switching exhibits will discard your unsaved description.",
          "Discard changes",
        );
        if (!active || rfidEditId !== editId) return;
        if (!discard) {
          rfidSelect.value = rfidSelectedId;
          return;
        }
      } catch (error) {
        if (active && rfidEditId === editId) {
          rfidSelect.value = rfidSelectedId;
          rfidEditMessage.textContent = `Could not switch exhibits: ${error.message}`;
        }
        return;
      } finally {
        if (active && rfidEditId === editId) {
          rfidSelect.disabled = false;
          rfidDescription.disabled = false;
          rfidSave.disabled = false;
        }
      }
    }
    rfidSelectedId = nextId;
    const profile = rfidProfiles.find((item) => item.id === rfidSelect.value);
    rfidDescription.value = profile?.description || "";
    rfidDirty = false;
    ctx.setDirty(false);
    rfidEditMessage.textContent = profile ? "Edit the description, then save." : "Select an exhibit.";
  }, { signal });
  rfidDescription.addEventListener("input", () => {
    const profile = rfidProfiles.find((item) => item.id === rfidSelect.value);
    rfidDirty = rfidDescription.value !== (profile?.description || "");
    ctx.setDirty(rfidDirty);
  }, { signal });
  rfidForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const profile = rfidProfiles.find((item) => item.id === rfidSelect.value);
    const description = rfidDescription.value.trim();
    if (!profile || !rfidEditId || !/^[\x20-\x7e]{1,180}$/.test(description)) {
      rfidEditMessage.textContent = "Select an exhibit and enter 1–180 printable English characters.";
      return;
    }
    const editId = rfidEditId;
    rfidSaving = true;
    rfidSelect.disabled = true;
    rfidDescription.disabled = true;
    rfidSave.disabled = true;
    let profileSaved = false;
    try {
      await ctx.api.saveExhibit({ ...profile, description });
      profileSaved = true;
      const response = await fetch("/api/rfid/publish-description", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edit_id: editId, exhibit_id: profile.id, description }),
        signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
      rfidDirty = false;
      ctx.setDirty(false);
      rfidEditMessage.textContent = "Description saved on the server. ESP32 will try to update the e-paper automatically.";
      rfidDelivery.textContent = "Description saved; waiting for the ESP32 and Mega to confirm the display update.";
      rfidSaving = false;
      await refreshRfidStatus();
    } catch (error) {
      if (error.name !== "AbortError") {
        rfidEditMessage.textContent = `${profileSaved ? "Exhibit saved locally, but the e-paper update was not saved. " : ""}${error.message}`;
      }
    } finally {
      rfidSaving = false;
      if (active && rfidEditId === editId) {
        rfidSelect.disabled = false;
        rfidDescription.disabled = false;
        rfidSave.disabled = false;
      }
    }
  }, { signal });

  rfidTestButtons.forEach((button) => {
    button.addEventListener(
      "click",
      async () => {
        const role = button.dataset.rfidTestRole;
        rfidTestButtons.forEach((testButton) => { testButton.disabled = true; });
        rfidEventMessage.textContent = `Sending simulated ${role} card tap…`;
        try {
          const response = await fetch("/api/rfid/device-event", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              type: "rfid_event",
              uid: role === "door" ? "A1B2C3D4" : "AABBCCDD",
              result: "accepted",
              exhibit_id: "helmet-001",
              role,
            }),
            signal,
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
          rfidEvent.textContent = JSON.stringify(result, null, 2);
          await refreshRfidStatus();
          if (active) rfidEventMessage.textContent = `Simulated ${role} card tap sent. Check the workflow status above.`;
        } catch (error) {
          if (error.name !== "AbortError") rfidEventMessage.textContent = error.message;
        } finally {
          if (active) rfidTestButtons.forEach((testButton) => { testButton.disabled = false; });
        }
      },
      { signal },
    );
  });
  rfidExamplePost.addEventListener(
    "click",
    async () => {
      rfidExamplePost.disabled = true;
      try {
        const response = await fetch("/api/rfid/device-event", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            type: "rfid_event",
            uid: "04833912EE1B90",
            result: "accepted",
            exhibit_id: "helmet-001",
          }),
          signal,
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
        rfidEvent.textContent = JSON.stringify(result, null, 2);
        await refreshRfidStatus();
        rfidEventMessage.textContent = "Example RFID event received; the latest event is included in workflow status.";
      } catch (error) {
        if (error.name !== "AbortError") rfidEventMessage.textContent = error.message;
      } finally {
        if (active) rfidExamplePost.disabled = false;
      }
    },
    { signal },
  );
  refreshRfidStatus();
  rfidPolling = window.setInterval(refreshRfidStatus, 3000);

  return () => {
    active = false;
    controller.abort();
    if (rfidPolling) window.clearInterval(rfidPolling);
    cancelAnimationFrame(animationFrame);
    if (modelContent) disposeModel(THREE, modelContent);
    if (renderer) {
      renderer.dispose();
      renderer.domElement.remove();
    }
  };
}
