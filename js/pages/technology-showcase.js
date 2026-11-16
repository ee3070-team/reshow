import { icon } from "../ui.js";
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

async function serializeJson(value) {
  const body = new TextEncoder().encode(JSON.stringify(value));
  const hash = await crypto.subtle.digest("SHA-256", body);
  const checksum = Array.from(new Uint8Array(hash), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return {
    body,
    headers: {
      "Content-Type": "application/json",
      "x-amz-content-sha256": checksum,
    },
  };
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

export async function render(container) {
  container.innerHTML = `<div class="stack technology-studio">
    <section class="card technology-intro">
      <div>
        <h2>${icon("cube")}3D & AI studio</h2>
        <p>Prepare a model preview or turn an artifact photo into structured exhibit data.</p>
      </div>
      <span class="badge info">Local showcase tools</span>
    </section>
    <div class="technology-grid">
      <section class="card technology-model">
        <div class="card-head"><h2>3D model studio</h2><span class="badge">Three.js</span></div>
        <div class="technology-viewer" data-viewer aria-label="Interactive 3D model preview"></div>
        <p class="technology-status" data-model-status role="status" aria-live="polite">Loading the 3D viewer…</p>
        <div class="technology-import">
          <label class="technology-field">Import a GLB or Three.js Group module
            <input data-model-file type="file" accept=".glb,.js,.mjs,model/gltf-binary,text/javascript">
          </label>
          <span class="muted small-text">Choose a .glb file or an ES module that exports a THREE.Group, THREE.Object3D, or THREE.Scene. Only import JavaScript modules you trust.</span>
        </div>
        <div class="technology-controls">
          <label>Zoom <output data-zoom-value>5.6</output>
            <input data-zoom type="range" min="3" max="10" step="0.1" value="5.6">
          </label>
          <label>Light intensity <output data-light-value>1.0</output>
            <input data-light type="range" min="0" max="2.5" step="0.1" value="1">
          </label>
        </div>
        <div class="technology-colors">
          <label>Pink light <input data-light-color="0" type="color" value="#ff69b4"></label>
          <label>Blue light <input data-light-color="1" type="color" value="#3498db"></label>
        </div>
        <div class="actions technology-actions">
          <button class="button" type="button" data-rotation aria-pressed="false">Pause rotation</button>
          <button class="button" type="button" data-angle="0">Front</button>
          <button class="button" type="button" data-angle="90">Side</button>
          <button class="button" type="button" data-angle="180">Back</button>
          <button class="button" type="button" data-angle="270">Side</button>
          <button class="button" type="button" data-fullscreen aria-pressed="false">Play 4-view fullscreen</button>
          <button class="button primary" type="button" data-export>Export four views</button>
        </div>
        <p class="technology-status" data-export-status role="status" aria-live="polite"></p>
      </section>
      <section class="card technology-ai">
        <div class="card-head"><h2>Image-to-JSON assistant</h2><span class="badge info">Workers AI</span></div>
        <p>Enter your Cloudflare credentials and choose an artifact image to generate a suggested name and description with example exhibit settings. Credentials and images are sent over HTTPS to the ReShow API and then to Cloudflare for this request only; they are not saved.</p>
        <label class="technology-credential">Cloudflare Account ID
          <input data-account-id type="text" inputmode="text" autocomplete="off" spellcheck="false" placeholder="Your 32-character account ID" required>
        </label>
        <label class="technology-credential">Cloudflare API token
          <span class="technology-token-input">
            <input data-api-token type="password" autocomplete="new-password" spellcheck="false" placeholder="Your Workers AI API token" required>
            <button class="button small" type="button" data-toggle-token aria-pressed="false">Show</button>
          </span>
        </label>
        <label class="technology-drop">
          <span>${icon("upload")}Choose a different JPEG, PNG, or WebP image</span>
          <input data-image-file type="file" accept="image/jpeg,image/png,image/webp">
        </label>
        <div class="technology-image-preview" data-image-preview role="img" aria-label="Selected image preview">Loading default helmet photo…</div>
        <button class="button primary" data-generate type="button" disabled>Generate exhibit JSON</button>
        <p class="technology-status" data-ai-status role="status" aria-live="polite">Select an image to generate exhibit data.</p>
        <pre class="technology-json" data-json-output aria-live="polite">Generated exhibit JSON will appear here.</pre>
        <section class="card technology-ai technology-image-3d">
          <div class="card-head"><h2>Image-to-3D assistant</h2><span class="badge info" data-3d-provider-label>Workers AI</span></div>
          <label class="technology-field">3D generation provider
            <select data-3d-provider>
              <option value="workers">Cloudflare Workers AI (three passes)</option>
            </select>
          </label>
        
          <p data-3d-provider-help>Build a Three.js Group in three passes—silhouette, structure, and details—and update the preview after each pass. This uses the same Cloudflare model, so it changes the workflow, not the model's underlying ability. Three passes use three Workers AI requests; free allowance and any charges depend on your Cloudflare plan.</p>
          <button class="button primary" data-generate-3d type="button" disabled>Generate Three.js Group</button>
          <p class="technology-status" data-image-3d-status role="status" aria-live="polite">Select an image above to generate a Three.js Group model.</p>
          <div class="technology-hunyuan-results" data-hunyuan-results aria-live="polite"></div>
        </section>
      </section>
      <section class="card technology-ai technology-rfid">
        <div class="card-head"><h2>ESP32/RFID workflow</h2><span class="badge info">Hardware</span></div>
        <pre class="technology-json" data-rfid-status aria-live="polite">Loading RFID workflow…</pre>
        <dl class="rfid-phase-guide">
          <div><dt>idle</dt><dd>No manual update is in progress. ESP32 is waiting for the first scan.</dd></div>
          <div><dt>pending_edit</dt><dd>The first scan was accepted. The employee manually updates the exhibit.</dd></div>
          <div><dt>ready_to_confirm</dt><dd>The employee marked the manual update finished. ESP32 waits for a matching second scan.</dd></div>
        </dl>
        <label class="technology-credential">Exhibit ID
          <output data-rfid-exhibit>Waiting for ESP32 scan</output>
        </label>
        <div class="actions">
          <button class="button" type="button" data-rfid-refresh>Refresh</button>
          <button class="button primary" type="button" data-rfid-ready>Mark manual update finished</button>
        </div>
        <p class="technology-status" data-rfid-message role="status" aria-live="polite">Waiting for the ESP32 workflow server.</p>
        <h3>RFID event GET/POST example</h3>
        <p class="technology-status">The example POST records a demo event without changing the workflow phase. GET returns that event in the same JSON shape.</p>
        <div class="actions">
          <button class="button" type="button" data-rfid-example-post>Send example RFID event</button>
          <button class="button" type="button" data-rfid-event-get>GET latest RFID event</button>
        </div>
        <pre class="technology-json" data-rfid-event aria-live="polite">No RFID event loaded.</pre>
        <p class="technology-status" data-rfid-event-message role="status" aria-live="polite"></p>
      </section>
    </div>
  </div>`;

  const controller = new AbortController();
  const { signal } = controller;
  const viewer = container.querySelector("[data-viewer]");
  const modelStatus = container.querySelector("[data-model-status]");
  const exportStatus = container.querySelector("[data-export-status]");
  const zoom = container.querySelector("[data-zoom]");
  const zoomValue = container.querySelector("[data-zoom-value]");
  const intensity = container.querySelector("[data-light]");
  const intensityValue = container.querySelector("[data-light-value]");
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

  try {
    THREE = await loadThree();
    if (!active) return;
    const scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(
      45,
      viewer.clientWidth / viewer.clientHeight,
      0.1,
      1000,
    );
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(viewer.clientWidth, viewer.clientHeight);
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

    const syncFullscreen = () => {
      const isFullscreen = document.fullscreenElement === viewer;
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
          document.exitFullscreen().catch((error) => {
            exportStatus.textContent =
              `Could not close fullscreen after a display error: ${error.message}`;
          });
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
          () => {
            document.exitFullscreen().catch((error) => {
              exportStatus.textContent =
                error instanceof Error && error.message
                  ? `Could not exit fullscreen: ${error.message}`
                  : "Could not exit fullscreen. Press Escape to close it.";
            });
          },
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
    fullscreenButton.addEventListener(
      "click",
      async () => {
        if (!renderer || !camera || !model) {
          exportStatus.textContent = "The 3D viewer is not ready yet.";
          return;
        }
        if (!viewer.requestFullscreen) {
          exportStatus.textContent =
            "Fullscreen playback is not supported by this browser.";
          return;
        }
        try {
          await viewer.requestFullscreen();
        } catch (error) {
          exportStatus.textContent =
            error instanceof Error && error.message
              ? `Could not enter fullscreen: ${error.message}`
              : "Could not enter fullscreen. Please try again.";
        }
      },
      { signal },
    );

    const renderFrame = () => {
      if (!active) return;
      if (autoRotate && !dragging) {
        model.rotation.y += 0.005;
        model.rotation.x += 0.002;
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
    container.querySelector("[data-rotation]").addEventListener(
      "click",
      (event) => {
        autoRotate = !autoRotate;
        event.currentTarget.textContent = autoRotate
          ? "Pause rotation"
          : "Resume rotation";
        event.currentTarget.setAttribute("aria-pressed", String(!autoRotate));
      },
      { signal },
    );
    container.querySelectorAll("[data-angle]").forEach((button) => {
      button.addEventListener(
        "click",
        () => {
          cameraAngle = Number(button.dataset.angle);
          updateCamera();
        },
        { signal },
      );
    });
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
        if (file.size > 25 * 1024 * 1024) {
          modelStatus.textContent = "Choose a GLB model smaller than 25 MB.";
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
  const image3DGenerate = container.querySelector("[data-generate-3d]");
  const image3DStatus = container.querySelector("[data-image-3d-status]");
  const image3DProvider = container.querySelector("[data-3d-provider]");
  const image3DProviderLabel = container.querySelector("[data-3d-provider-label]");
  const image3DProviderHelp = container.querySelector("[data-3d-provider-help]");
  
  image3DProviderLabel.textContent = "Cloudflare Workers AI";
  const loadImageFile = (file) =>
    new Promise((resolve, reject) => {
      const image = new Image();
      const url = URL.createObjectURL(file);
      image.onload = () => {
        URL.revokeObjectURL(url);
        if (!active) return resolve(false);
        loadedImage = image;
        preview.replaceChildren(image);
        image.alt = "Selected artifact image preview";
        generateButton.disabled = false;
        image3DGenerate.disabled = false;
        aiStatus.textContent = "Image ready. Generate exhibit JSON when ready.";
        image3DStatus.textContent = image3DProvider.value === "hunyuan"
          ? "Image ready. Enter Tencent credentials to generate a 3D model."
          : "Image ready. Generate a Three.js Group with Workers AI.";
        resolve(true);
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("The selected image could not be decoded."));
      };
      image.src = url;
    });
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
      image3DGenerate.disabled = true;
      hunyuanResults.replaceChildren();
      jsonOutput.textContent = "Generated exhibit JSON will appear here.";
      if (!selectedImage) {
        preview.textContent = "No image selected";
        aiStatus.textContent = "Select an image to generate exhibit data.";
        image3DStatus.textContent =
          "Select an image above to generate a Three.js Group model.";
        return;
      }
      if (
        !["image/jpeg", "image/png", "image/webp"].includes(selectedImage.type)
      ) {
        selectedImage = null;
        preview.textContent = "No image selected";
        aiStatus.textContent = "Choose a JPEG, PNG, or WebP image.";
        image3DStatus.textContent = "Choose a JPEG, PNG, or WebP image.";
        return;
      }
      loadImageFile(selectedImage).catch((error) => {
        preview.textContent = "No image selected";
        aiStatus.textContent = error.message;
        image3DStatus.textContent = error.message;
      });
    },
    { signal },
  );
  fetch("assets/helmet.jpg", { signal })
    .then((response) => {
      if (!response.ok)
        throw new Error(`Could not load the default image (HTTP ${response.status}).`);
      return response.blob();
    })
    .then((blob) => {
      if (!active || selectedImage) return;
      if (blob.type !== "image/jpeg")
        throw new Error("The default helmet image is not a JPEG.");
      const file = new File([blob], "helmet.jpg", { type: blob.type });
      selectedImage = file;
      return loadImageFile(file);
    })
    .catch((error) => {
      if (!active || error.name === "AbortError") return;
      preview.textContent = "No image selected";
      aiStatus.textContent = error.message;
      image3DStatus.textContent = error.message;
    });

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
          aiStatus.textContent = error.message;
        }
      } finally {
        if (active) generateButton.disabled = !loadedImage;
      }
    },
    { signal },
  );

  const applyGeneratedModel = (parts, minimumParts, isFallback = false) => {
    if (!THREE || !model || !modelContent) {
      throw new Error("The 3D viewer is not ready yet.");
    }
    if (!Array.isArray(parts) || parts.length > 12) {
      throw new Error("Workers AI returned an invalid Three.js Group.");
    }
    if (!isFallback && parts.length < minimumParts) {
      throw new Error("Workers AI returned an invalid Three.js Group.");
    }
    if (parts.filter((part) => part?.shape === "sphere").length > 3) {
      throw new Error("Workers AI returned a sphere-heavy Three.js Group.");
    }
    const generated = new THREE.Group();
    for (const part of parts) {
      if (
        !part ||
        !["sphere", "box", "cylinder"].includes(part.shape) ||
        !Array.isArray(part.position) ||
        part.position.length !== 3 ||
        !Array.isArray(part.scale) ||
        part.scale.length !== 3 ||
        typeof part.color !== "string"
      ) {
        throw new Error("Workers AI returned an invalid Three.js Group.");
      }
      const geometry =
        part.shape === "box"
          ? new THREE.BoxGeometry(1, 1, 1)
          : part.shape === "cylinder"
            ? new THREE.CylinderGeometry(0.5, 0.5, 1, 16)
            : new THREE.SphereGeometry(0.5, 20, 16);
      const material = new THREE.MeshStandardMaterial({
        color: part.color,
        roughness: 0.8,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...part.position);
      mesh.scale.set(...part.scale);
      generated.add(mesh);
    }
    generated.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(generated);
    const size = bounds.getSize(new THREE.Vector3());
    const largest = Math.max(size.x, size.y, size.z);
    if (!Number.isFinite(largest) || largest <= 0) {
      disposeModel(THREE, generated);
      throw new Error("Workers AI returned a model with no displayable geometry.");
    }
    generated.scale.multiplyScalar(2.8 / largest);
    generated.updateMatrixWorld(true);
    generated.position.sub(
      new THREE.Box3()
        .setFromObject(generated)
        .getCenter(new THREE.Vector3()),
    );
    model.remove(modelContent);
    disposeModel(THREE, modelContent);
    modelContent = generated;
    model.add(modelContent);
    model.rotation.set(0, 0, 0);
    targetY = 0;
    cameraAngle = 0;
    updateCamera();
    modelStatus.textContent =
      "Workers AI generated a Three.js Group from the selected image.";
  };

  const generateWithHunyuan = async () => {
    const secretId = hunyuanSecretId.value.trim();
    const secretKey = hunyuanSecretKey.value;
    const region = hunyuanRegion.value;
    if (!secretId || !secretKey) {
      image3DStatus.textContent = "Enter your Tencent Cloud SecretId and SecretKey.";
      (secretId ? hunyuanSecretKey : hunyuanSecretId).focus();
      return;
    }

    image3DGenerate.disabled = true;
    image3DProvider.disabled = true;
    hunyuanResults.replaceChildren();
    try {
      const maxDimension = 768;
      let scale = Math.min(
        1,
        maxDimension / Math.max(loadedImage.width, loadedImage.height),
      );
      if (Math.min(loadedImage.width, loadedImage.height) * scale < 128) {
        scale = 128 / Math.min(loadedImage.width, loadedImage.height);
      }
      if (Math.max(loadedImage.width, loadedImage.height) * scale > 5000) {
        throw new Error("The image aspect ratio is outside Tencent's supported size.");
      }
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(loadedImage.width * scale));
      canvas.height = Math.max(1, Math.round(loadedImage.height * scale));
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Could not prepare the image.");
      context.drawImage(loadedImage, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.85),
      );
      if (!blob) throw new Error("Could not prepare the image.");
      if (blob.size > 4 * 1024 * 1024) {
        throw new Error("The prepared image is larger than 4 MB.");
      }

      image3DStatus.textContent = "Submitting the image to Tencent Hunyuan 3D Rapid…";
      const form = new FormData();
      form.append("image", blob, "image.jpg");
      form.append("secret_id", secretId);
      form.append("secret_key", secretKey);
      form.append("region", region);
      const upload = await serializeUpload(form);
      const submitResponse = await fetch(HUNYUAN_SUBMIT_PATH, {
        method: "POST",
        ...upload,
        signal,
      });
      const submitResult = await submitResponse.json().catch(() => ({}));
      if (!submitResponse.ok) {
        throw new Error(
          submitResult.error || `Tencent request failed (HTTP ${submitResponse.status}).`,
        );
      }
      const jobId = submitResult.data?.job_id;
      if (typeof jobId !== "string" || !jobId) {
        throw new Error("Tencent returned no valid job ID.");
      }

      const deadline = Date.now() + 15 * 60 * 1000;
      let completed = false;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        const query = await serializeJson({
          job_id: jobId,
          secret_id: secretId,
          secret_key: secretKey,
          region,
        });
        const queryResponse = await fetch(HUNYUAN_QUERY_PATH, {
          method: "POST",
          ...query,
          signal,
        });
        const queryResult = await queryResponse.json().catch(() => ({}));
        if (!queryResponse.ok) {
          throw new Error(
            queryResult.error || `Tencent query failed (HTTP ${queryResponse.status}).`,
          );
        }
        const data = queryResult.data;
        if (!data || !["WAIT", "RUN", "FAIL", "DONE"].includes(data.status)) {
          throw new Error("Tencent returned an invalid job status.");
        }
        if (data.status === "FAIL") {
          throw new Error("Tencent Hunyuan failed to generate the model. Check model access and account quota.");
        }
        if (data.status === "DONE") {
          const files = Array.isArray(data.files) ? data.files : [];
          for (const [index, file] of files.entries()) {
            if (typeof file?.url !== "string") continue;
            const url = new URL(file.url);
            if (url.protocol !== "https:") continue;
            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = `Download Tencent 3D result ${index + 1}${file.type ? ` (${file.type})` : ""}`;
            hunyuanResults.append(link);
          }
          if (files.length) {
            const note = document.createElement("p");
            note.className = "technology-status";
            note.textContent =
              "If the download is a ZIP, extract its GLB file and import it in the 3D model studio.";
            hunyuanResults.append(note);
            image3DStatus.textContent =
              "Tencent Hunyuan finished generating the model.";
          } else {
            image3DStatus.textContent =
              "Tencent Hunyuan finished, but returned no downloadable model URL.";
          }
          completed = true;
          break;
        }
        image3DStatus.textContent =
          `Tencent Hunyuan is processing the image (${data.status}). Checking again…`;
      }
      if (!completed) {
        throw new Error("The Tencent job is still running. Try again later with the same JobId.");
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        console.error("Tencent Hunyuan 3D request failed.");
        image3DStatus.textContent = error.message;
      }
    } finally {
      hunyuanSecretId.value = "";
      hunyuanSecretKey.value = "";
      image3DProvider.disabled = false;
      if (active) image3DGenerate.disabled = !loadedImage;
    }
  };

  image3DGenerate.addEventListener(
    "click",
    async () => {
      if (!loadedImage || !selectedImage) {
        image3DStatus.textContent =
          "Select an image above before generating a 3D model.";
        return;
      }
      if (image3DProvider.value === "hunyuan") {
        await generateWithHunyuan();
        return;
      }
      const accountId = accountIdInput.value.trim();
      const apiToken = apiTokenInput.value.trim();
      if (!/^[a-f\d]{32}$/i.test(accountId)) {
        image3DStatus.textContent =
          "Enter a valid 32-character Cloudflare Account ID in the Image-to-JSON assistant.";
        accountIdInput.focus();
        return;
      }
      if (!apiToken) {
        image3DStatus.textContent =
          "Enter your Cloudflare API token in the Image-to-JSON assistant.";
        apiTokenInput.focus();
        return;
      }
      image3DGenerate.disabled = true;
      const stages = [
        {
          name: "silhouette",
          minimumParts: 3,
          label: "Step 1/3: building the silhouette",
        },
        {
          name: "structure",
          minimumParts: 6,
          label: "Step 2/3: adding the main structures",
        },
        {
          name: "details",
          minimumParts: 8,
          label: "Step 3/3: refining visible details",
        },
      ];
      let completedParts = [];
      let activeStage = "";
      try {
        const scale = Math.min(
          1,
          768 / Math.max(loadedImage.width, loadedImage.height),
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
        for (const [index, stage] of stages.entries()) {
          activeStage = stage.label;
          image3DStatus.textContent = `${stage.label}…`;
          const data = new FormData();
          data.append("image", blob, "image.jpg");
          data.append("account_id", accountId);
          data.append("api_token", apiToken);
          data.append("task", "threejs_group");
          data.append("stage", stage.name);
          data.append("previous_parts", JSON.stringify(completedParts));
          const upload = await serializeUpload(data);
          const response = await fetch("/api/technology/generate-exhibit", {
            method: "POST",
            ...upload,
            signal,
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok)
            throw new Error(
              result.error || `Request failed (HTTP ${response.status}).`,
            );
          if (
            !result.data ||
            typeof result.data !== "object" ||
            Array.isArray(result.data) ||
            (result.data.stage !== undefined &&
              result.data.stage !== stage.name)
          ) {
            throw new Error("The server returned an invalid 3D model step.");
          }
          applyGeneratedModel(
            result.data.parts,
            stage.minimumParts,
            !!result.data.fallback
          );
          completedParts = result.data.parts;
          if (result.data.fallback) {
            image3DStatus.textContent = `${stage.label} complete (fallback model).`;
          } else {
            image3DStatus.textContent = `${stage.label} complete (${index + 1}/${stages.length}).`;
          }
        }
        activeStage = "";
        image3DStatus.textContent =
          "Three.js Group generated step by step and loaded into the model viewer.";
      } catch (error) {
        if (error.name !== "AbortError") {
          console.error("Image-to-3D generation failed.", error);
          image3DStatus.textContent = activeStage
            ? `${activeStage} failed. The latest completed stage remains in the preview. ${error.message}`
            : error.message;
        }
      } finally {
        if (active) image3DGenerate.disabled = !loadedImage;
      }
    },
    { signal },
  );

  const rfidStatus = container.querySelector("[data-rfid-status]");
  const rfidMessage = container.querySelector("[data-rfid-message]");
  const rfidRefresh = container.querySelector("[data-rfid-refresh]");
  const rfidReady = container.querySelector("[data-rfid-ready]");
  const rfidExhibit = container.querySelector("[data-rfid-exhibit]");
  const rfidExamplePost = container.querySelector("[data-rfid-example-post]");
  const rfidEventGet = container.querySelector("[data-rfid-event-get]");
  const rfidEvent = container.querySelector("[data-rfid-event]");
  const rfidEventMessage = container.querySelector("[data-rfid-event-message]");
  let rfidPolling;

  const refreshRfidStatus = async () => {
    try {
      const response = await fetch("/api/rfid/status", { signal });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
      const workflow = result.data || {};
      rfidStatus.textContent = JSON.stringify(workflow, null, 2);
      rfidExhibit.textContent = workflow.exhibit_id || "Waiting for ESP32 scan";
      rfidMessage.textContent = "ESP32 workflow status is available.";
    } catch (error) {
      if (error.name === "AbortError") return;
      rfidStatus.textContent = '{"phase":"unavailable"}';
      rfidMessage.textContent = error.message;
    }
  };

  rfidRefresh.addEventListener("click", refreshRfidStatus, { signal });
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
        rfidEventMessage.textContent = "Example RFID event POST received. Use GET to retrieve the latest event.";
      } catch (error) {
        if (error.name !== "AbortError") rfidEventMessage.textContent = error.message;
      } finally {
        if (active) rfidExamplePost.disabled = false;
      }
    },
    { signal },
  );
  rfidEventGet.addEventListener(
    "click",
    async () => {
      rfidEventGet.disabled = true;
      try {
        const response = await fetch("/api/rfid/device-event", { signal });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
        rfidEvent.textContent = JSON.stringify(result, null, 2);
        rfidEventMessage.textContent = "Latest RFID event returned by GET.";
      } catch (error) {
        if (error.name !== "AbortError") rfidEventMessage.textContent = error.message;
      } finally {
        if (active) rfidEventGet.disabled = false;
      }
    },
    { signal },
  );
  rfidReady.addEventListener(
    "click",
    async () => {
    rfidReady.disabled = true;
    try {
      const response = await fetch("/api/rfid/staff-ready", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
        signal,
      });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || `RFID request failed (HTTP ${response.status}).`);
        rfidStatus.textContent = JSON.stringify(result.data || {}, null, 2);
        rfidMessage.textContent = "Manual update marked finished; ESP32 now waits for the matching second scan.";
      } catch (error) {
        if (error.name !== "AbortError") rfidMessage.textContent = error.message;
      } finally {
        if (active) rfidReady.disabled = false;
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
