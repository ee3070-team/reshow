import * as THREE from 'https://jsdelivr.net';

const group = new THREE.Group();
group.name = 'CustomGroup';

// 底座
const base = new THREE.Mesh(
  new THREE.CylinderGeometry(1.2, 1.5, 0.4, 32),
  new THREE.MeshStandardMaterial({ color: 0x444444 })
);
base.position.y = 0.2;

// 主體
const body = new THREE.Mesh(
  new THREE.BoxGeometry(1, 1.5, 1),
  new THREE.MeshStandardMaterial({ color: 0x0088ff })
);
body.position.y = 1.15;

// 頂部
const top = new THREE.Mesh(
  new THREE.SphereGeometry(0.4, 32, 32),
  new THREE.MeshStandardMaterial({ color: 0xffaa00 })
);
top.position.y = 2.2;

group.add(base, body, top);

export { group };
export default group;
