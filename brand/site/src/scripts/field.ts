/**
 * Fond de particules OGL : de la « poussière dorée » (Blé) et quelques éclats Casaque
 * qui dérivent lentement et s'écartent du curseur.
 * Chargé à la demande (import dynamique), arrêté hors écran et onglet masqué.
 */
import { Renderer, Camera, Geometry, Program, Mesh } from "ogl";

const vertex = /* glsl */ `
  attribute vec3 position;
  attribute vec4 random;
  uniform float uTime;
  uniform vec2 uMouse;
  uniform float uAspect;
  uniform float uSize;
  varying vec4 vRandom;
  void main() {
    vRandom = random;
    vec3 p = position;
    float t = uTime * 0.08;
    p.x += sin(t * random.y * 6.28 + random.x * 6.28) * 0.08;
    p.y += cos(t * random.z * 6.28 + random.w * 6.28) * 0.06;
    vec2 d = vec2((p.x - uMouse.x) * uAspect, p.y - uMouse.y);
    float l = length(d);
    p.xy += normalize(d + 1e-4) * smoothstep(0.42, 0.0, l) * 0.16 / vec2(uAspect, 1.0);
    gl_Position = vec4(p.xy, 0.0, 1.0);
    gl_PointSize = uSize * (0.35 + random.x * 1.1);
  }
`;

const fragment = /* glsl */ `
  precision highp float;
  varying vec4 vRandom;
  uniform vec3 uBle;
  uniform vec3 uCasaque;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv);
    float ring = smoothstep(0.5, 0.42, d) * smoothstep(0.26, 0.34, d);
    float disc = smoothstep(0.5, 0.36, d);
    bool isRing = vRandom.z > 0.86;
    float a = isRing ? ring : disc;
    vec3 col = vRandom.y > 0.82 ? uCasaque : uBle;
    gl_FragColor = vec4(col, a * (0.22 + vRandom.w * 0.4));
  }
`;

const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);

export function mountField(canvas: HTMLCanvasElement, count = 90) {
  const renderer = new Renderer({ canvas, alpha: true, antialias: false, dpr: Math.min(devicePixelRatio, 1.5), premultipliedAlpha: false });
  const gl = renderer.gl;
  gl.clearColor(0, 0, 0, 0);
  const camera = new Camera(gl);

  const position = new Float32Array(count * 3);
  const random = new Float32Array(count * 4);
  for (let i = 0; i < count; i++) {
    position.set([Math.random() * 2 - 1, Math.random() * 2 - 1, 0], i * 3);
    random.set([Math.random(), Math.random(), Math.random(), Math.random()], i * 4);
  }
  const geometry = new Geometry(gl, {
    position: { size: 3, data: position },
    random: { size: 4, data: random },
  });
  const program = new Program(gl, {
    vertex,
    fragment,
    transparent: true,
    depthTest: false,
    uniforms: {
      uTime: { value: 0 },
      uMouse: { value: [9, 9] },
      uAspect: { value: 1 },
      uSize: { value: 14 },
      uBle: { value: hex("#E6A752") },
      uCasaque: { value: hex("#1F5A48") },
    },
  });
  const mesh = new Mesh(gl, { mode: gl.POINTS, geometry, program });

  const target = [9, 9];
  // OGL fixe une taille inline au canvas : on mesure donc le conteneur.
  const host = canvas.parentElement ?? canvas;
  const resize = () => {
    const r = host.getBoundingClientRect();
    renderer.setSize(r.width, r.height);
    program.uniforms.uAspect.value = r.width / Math.max(r.height, 1);
    program.uniforms.uSize.value = Math.max(6, Math.min(14, r.width / 110)) * renderer.dpr;
  };
  const onMove = (e: PointerEvent) => {
    const r = canvas.getBoundingClientRect();
    target[0] = ((e.clientX - r.left) / r.width) * 2 - 1;
    target[1] = -(((e.clientY - r.top) / r.height) * 2 - 1);
  };

  let raf = 0;
  let visible = true;
  let start = performance.now();
  const loop = (t: number) => {
    raf = requestAnimationFrame(loop);
    const m = program.uniforms.uMouse.value as number[];
    m[0] += (target[0] - m[0]) * 0.06;
    m[1] += (target[1] - m[1]) * 0.06;
    program.uniforms.uTime.value = (t - start) / 1000;
    renderer.render({ scene: mesh, camera });
  };
  const play = () => {
    cancelAnimationFrame(raf);
    if (visible && !document.hidden) raf = requestAnimationFrame(loop);
  };

  const io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    play();
  });
  const ro = new ResizeObserver(resize);
  const onVis = () => play();

  io.observe(canvas);
  ro.observe(host);
  window.addEventListener("pointermove", onMove, { passive: true });
  document.addEventListener("visibilitychange", onVis);
  resize();
  canvas.classList.add("is-ready");
  play();

  return () => {
    cancelAnimationFrame(raf);
    io.disconnect();
    ro.disconnect();
    window.removeEventListener("pointermove", onMove);
    document.removeEventListener("visibilitychange", onVis);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
  };
}
