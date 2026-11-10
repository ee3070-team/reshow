import { icon } from "../ui.js";
import { serializeUpload } from "../technology-upload.js";

const THREE_URL =
  "js/vendor/three-r128/three.min.js";
const GLTF_LOADER_URL =
  "js/vendor/three-r128/GLTFLoader.js";
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
          <label class="technology-field">Import a GLB model
            <input data-model-file type="file" accept=".glb,model/gltf-binary">
          </label>
          <span class="muted small-text">Choose a .glb file that includes the model and textures.</span>
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

    const resize = new ResizeObserver(() => {
      if (!active || !renderer) return;
      const width = Math.max(1, viewer.clientWidth);
      const height = Math.max(1, viewer.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    });
    resize.observe(viewer);

    const renderFrame = () => {
      if (!active) return;
      if (autoRotate && !dragging) {
        model.rotation.y += 0.005;
        model.rotation.x += 0.002;
      }
      renderer.render(scene, camera);
      animationFrame = requestAnimationFrame(renderFrame);
    };
    renderFrame();

    viewer.addEventListener(
      "pointerdown",
      (event) => {
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
      (event) => {
        const file = event.currentTarget.files?.[0];
        event.currentTarget.value = "";
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".glb")) {
          modelStatus.textContent = "Choose a .glb model file.";
          return;
        }
        if (!THREE.GLTFLoader) {
          modelStatus.textContent = "The 3D model loader is unavailable.";
          return;
        }
        modelStatus.textContent = `Loading ${file.name}…`;
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
        const size = 480;
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
        const views = [
          { angle: 0, cell: [1, 0], rotation: 0 },
          { angle: 90, cell: [2, 1], rotation: Math.PI / 2 },
          { angle: 180, cell: [1, 2], rotation: Math.PI },
          { angle: 270, cell: [0, 1], rotation: -Math.PI / 2 },
        ];
        try {
          context.fillStyle = "#000";
          context.fillRect(0, 0, output.width, output.height);
          model.rotation.set(0, 0, 0);
          renderer.setSize(size, size, false);
          camera.aspect = 1;
          camera.updateProjectionMatrix();
          for (const view of views) {
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
        aiStatus.textContent = "Image ready. Generate exhibit JSON when ready.";
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
      jsonOutput.textContent = "Generated exhibit JSON will appear here.";
      if (!selectedImage) {
        preview.textContent = "No image selected";
        aiStatus.textContent = "Select an image to generate exhibit data.";
        return;
      }
      if (
        !["image/jpeg", "image/png", "image/webp"].includes(selectedImage.type)
      ) {
        selectedImage = null;
        preview.textContent = "No image selected";
        aiStatus.textContent = "Choose a JPEG, PNG, or WebP image.";
        return;
      }
      loadImageFile(selectedImage).catch((error) => {
        preview.textContent = "No image selected";
        aiStatus.textContent = error.message;
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

  return () => {
    active = false;
    controller.abort();
    cancelAnimationFrame(animationFrame);
    if (modelContent) disposeModel(THREE, modelContent);
    if (renderer) {
      renderer.dispose();
      renderer.domElement.remove();
    }
  };
}
