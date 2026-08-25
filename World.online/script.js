const header = document.querySelector('.site-header');
const clockLabels = document.querySelectorAll('[data-clock]');

function updateClock() {
  const time = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  }).format(new Date());
  clockLabels.forEach((label) => { label.textContent = `${time} UTC+08`; });
}

updateClock();
setInterval(updateClock, 1000);
window.addEventListener('scroll', () => {
  header?.classList.toggle('header-solid', window.scrollY > 24);
});

const hero = document.querySelector('.network-hero');

document.querySelectorAll('.filter').forEach((filter) => {
  filter.addEventListener('click', () => {
    document.querySelector('.filter.active')?.classList.remove('active');
    filter.classList.add('active');
  });
});

const polyCanvas = document.querySelector('.polyhedron-canvas');
if (polyCanvas && hero) {
  const context = polyCanvas.getContext('2d');
  const cube = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
  const octa = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]];
  const tetra = [[1,1,1],[-1,-1,1],[-1,1,-1],[1,-1,-1]];
  const ico = (() => { const p=(1+Math.sqrt(5))/2; return [[-1,p,0],[1,p,0],[-1,-p,0],[1,-p,0],[0,-1,p],[0,1,p],[0,-1,-p],[0,1,-p],[p,0,-1],[p,0,1],[-p,0,-1],[-p,0,1]]; })();
  const irregular = [[-1.2,-.45,-.3],[.8,-.9,.45],[1.35,.24,-.55],[.38,1.1,.85],[-.85,.72,.4],[-.38,-.12,1.35],[.15,.3,-1.25]];
  const edgePairs = (points) => { const distances=[]; for(let a=0;a<points.length;a++) for(let b=a+1;b<points.length;b++) distances.push([a,b,Math.hypot(points[a][0]-points[b][0],points[a][1]-points[b][1],points[a][2]-points[b][2])]); const min=Math.min(...distances.map(([, ,d])=>d)); return distances.filter(([, ,d])=>d<min*1.08).map(([a,b])=>[a,b]); };
  const randomBetween = (min, max) => min + Math.random() * (max - min);
  const makeIrregular = (count = 7) => Array.from({ length:count }, () => [randomBetween(-1.25,1.25),randomBetween(-1.25,1.25),randomBetween(-1.25,1.25)]);
  const randomEdges = (points) => { const edges=edgePairs(points); for(let i=0;i<points.length;i++){ const target=(i+2+Math.floor(Math.random()*2))%points.length; if(!edges.some(([a,b])=>(a===i&&b===target)||(a===target&&b===i))) edges.push([i,target]); } return edges; };
  const objects = [
    { points:tetra, edges:edgePairs(tetra), x:.20,y:.27,size:34, speed:.00075, direction:1, phase:.2, color:[100,220,255], opacity:.42 },
    { points:cube, edges:edgePairs(cube), x:.43,y:.77,size:28, speed:.00048, direction:-1, phase:1.3, color:[183,143,255], opacity:.36 },
    { points:octa, edges:edgePairs(octa), x:.84,y:.18,size:27, speed:.00066, direction:1, phase:2.1, color:[98,202,255], opacity:.34 },
    { points:ico, edges:edgePairs(ico), x:.13,y:.73,size:35, speed:.00036, direction:-1, phase:3, color:[151,132,255], opacity:.38 },
    { points:irregular, edges:[[0,1],[1,2],[2,3],[3,4],[4,0],[0,5],[1,5],[3,5],[4,5],[0,6],[2,6],[3,6],[4,6],[1,3]], x:.33,y:.15,size:26, speed:.00054, direction:1, phase:4, color:[89,206,255], opacity:.24 }
  ];
  const createAmbientPolyhedron = (index = 0) => {
    const side = Math.floor(Math.random() * 4);
    const edgeOffset = randomBetween(.08, .92);
    const start = side === 0 ? [-.13, edgeOffset] : side === 1 ? [1.13, edgeOffset] : side === 2 ? [edgeOffset, -.13] : [edgeOffset, 1.13];
    const target = side === 0 ? [1.13, randomBetween(.12, .88)] : side === 1 ? [-.13, randomBetween(.12, .88)] : side === 2 ? [randomBetween(.12, .88), 1.13] : [randomBetween(.12, .88), -.13];
    const duration = randomBetween(26000, 42000);
    return {
      points: makeIrregular(6 + Math.floor(Math.random() * 4)),
      x: start[0], y: start[1], start, target, born: performance.now(), duration,
      edges: [], size: randomBetween(15, 25), speed: randomBetween(.00022, .00062),
      direction: Math.random() > .5 ? 1 : -1, phase: Math.random() * Math.PI * 2,
      color: index % 2 ? [168,130,255] : [82,202,255], opacity: randomBetween(.52,.68), cycle: randomBetween(4800,7800), ambient: true
    };
  };
  const addAmbientPolyhedron = index => { const object = createAmbientPolyhedron(index); object.edges = randomEdges(object.points); objects.push(object); };
  for (let index = 0; index < 5; index++) addAmbientPolyhedron(index);
  const rotateRigid = ([x, y, z], rx, ry, rz) => { const cx=Math.cos(rx), sx=Math.sin(rx), cy=Math.cos(ry), sy=Math.sin(ry), cz=Math.cos(rz), sz=Math.sin(rz); const y1=y*cx-z*sx, z1=y*sx+z*cx; const x2=x*cy+z1*sy, z2=-x*sy+z1*cy; return [x2*cz-y1*sz, x2*sz+y1*cz, z2]; };
  let canvasWidth = 0, canvasHeight = 0;
  function resizeCanvas(){ const ratio=devicePixelRatio||1; const box=hero.getBoundingClientRect(); const width=Math.round(box.width*ratio), height=Math.round(box.height*ratio); if(width===canvasWidth && height===canvasHeight) return; canvasWidth=width; canvasHeight=height; polyCanvas.width=width; polyCanvas.height=height; polyCanvas.style.width=box.width + 'px'; polyCanvas.style.height=box.height + 'px'; context.setTransform(ratio,0,0,ratio,0,0); }
  function render(now){ const box=hero.getBoundingClientRect(); context.clearRect(0,0,box.width,box.height); for(let i=objects.length-1;i>=0;i--){ const object=objects[i]; if(object.ambient){ const progress=(now-object.born)/object.duration; if(progress>=1){ objects.splice(i,1); addAmbientPolyhedron(i); continue; } object.x=object.start[0]+(object.target[0]-object.start[0])*progress; object.y=object.start[1]+(object.target[1]-object.start[1])*progress; } } objects.forEach((object,index)=>{ const direction=object.direction ?? 1, a=now*object.speed*direction+index, b=now*object.speed*.73*direction+index*.8, c=now*object.speed*.49*direction; const visibility=object.cycle ? .68 + .32 * ((Math.sin(now/object.cycle*Math.PI*2+object.phase)+1)/2) : .82; const points=object.points.map(point=>{ const [x,y,z]=rotateRigid(point,a,b,c); const depth=4.5+z; return {x:box.width*object.x+x*object.size*4/depth,y:box.height*object.y+y*object.size*4/depth,z}; }); context.lineWidth=1.15; object.edges.forEach(([from,to])=>{const first=points[from],second=points[to],alpha=(.32+((first.z+second.z+2)/4)*.48)*object.opacity*visibility;context.strokeStyle=`rgba(${object.color.join(',')},${alpha})`;context.shadowColor=`rgba(${object.color.join(',')},.9)`;context.shadowBlur=10;context.beginPath();context.moveTo(first.x,first.y);context.lineTo(second.x,second.y);context.stroke();}); points.forEach(point=>{context.fillStyle=`rgba(${object.color.join(',')},${.35+(point.z+1)/4})`;context.beginPath();context.arc(point.x,point.y,1.05,0,Math.PI*2);context.fill();}); }); burstCanvasObjects=burstCanvasObjects.filter(object=>{ const delta=Math.min(34,Math.max(8,now-(object.lastNow??now))); object.lastNow=now; const age=now-object.born; const currentSpeed=Math.max(object.minSpeed,object.speed-object.deceleration*age-object.jerk*age*age); object.vx=object.directionX*currentSpeed; object.vy=object.directionY*currentSpeed; object.x+=object.vx*delta; object.y+=object.vy*delta; object.rotation=(object.rotation+object.rotationSpeed*delta)%(Math.PI*2); const points=object.points.map(point=>{ const [x,y,z]=rotateRigid(point,object.rotation,object.rotation*.73,object.rotation*.49); const depth=4.5+z; return {x:object.x+x*object.size*4/depth,y:object.y+y*object.size*4/depth,z}; }); const alpha=object.opacity*Math.min(1,age/180); context.lineWidth=1.15; object.edges.forEach(([from,to])=>{const first=points[from],second=points[to],edgeAlpha=(.32+((first.z+second.z+2)/4)*.48)*alpha;context.strokeStyle=`rgba(${object.color.join(',')},${edgeAlpha})`;context.shadowColor=`rgba(${object.color.join(',')},.9)`;context.shadowBlur=10;context.beginPath();context.moveTo(first.x,first.y);context.lineTo(second.x,second.y);context.stroke();}); points.forEach(point=>{context.fillStyle=`rgba(${object.color.join(',')},${.35+(point.z+1)/4})`;context.beginPath();context.arc(point.x,point.y,1.05,0,Math.PI*2);context.fill();}); const margin=object.size*3; return object.x>-margin&&object.x<box.width+margin&&object.y>-margin&&object.y<box.height+margin; }); context.shadowBlur=0;requestAnimationFrame(render); }
  resizeCanvas();addEventListener('resize',resizeCanvas);requestAnimationFrame(render);
}
const coreRectangle = document.querySelector('.geometry-diamond');
const corePoint = document.querySelector('.core');
const coreBlastRed = document.querySelector('.core-blast-red');
const coreBlastWhite = document.querySelector('.core-blast-white');
const coreShell = document.querySelector('.core-shell');
const coreShellPoints = document.querySelector('.core-shell-points');
const coreCenter = document.querySelector('.core-center');
const coreNextCenter = document.querySelector('.core-next-center');
const coreWaves = document.querySelectorAll('.core-wave');
const coreScan = document.querySelector('.geometry-scan');
const coreRingGuide = document.querySelector('.geometry-ring');
const burstLayer = document.querySelector('.core-particles');
let burstBodies = [];
let burstFrameActive = false;
let burstLastTime = 0;
let burstSpawned = false;
let burstCanvasObjects = [];
const randomBetween = (min, max) => min + Math.random() * (max - min);
const burstShapeTemplates = [
  { points: [[1,1,1],[-1,-1,1],[-1,1,-1],[1,-1,-1]], edges: [[0,1],[0,2],[0,3],[1,2],[1,3],[2,3]] },
  { points: [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]], edges: [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]] },
  { points: [[1,0,0],[.31,.95,0],[-.81,.59,0],[-.81,-.59,0],[.31,-.95,0]], edges: [[0,1],[1,2],[2,3],[3,4],[4,0]] },
  { points: [[-1.2,-.45,-.3],[.8,-.9,.45],[1.35,.24,-.55],[.38,1.1,.85],[-.85,.72,.4],[-.38,-.12,1.35],[.15,.3,-1.25]], edges: [[0,1],[1,2],[2,3],[3,4],[4,0],[0,5],[1,5],[3,5],[4,5],[0,6],[2,6],[3,6],[4,6],[1,3]] }
];
const spawnBurst = now => {
  const box = hero?.getBoundingClientRect();
  const coreRect = corePoint?.getBoundingClientRect();
  if (!box || !coreRect) return;
  const originX = coreRect.left - box.left + coreRect.width / 2;
  const originY = coreRect.top - box.top + coreRect.height / 2;
  const count = 10 + Math.floor(Math.random() * 7);
  for (let index = 0; index < count; index++) {
    const template = burstShapeTemplates[Math.floor(Math.random() * burstShapeTemplates.length)];
    const angle = randomBetween(0, Math.PI * 2);
    const speed = randomBetween(.16, .28);
    const minSpeed = randomBetween(.075, .11);
    const deceleration = randomBetween(.000035, .00007);
    const jerk = randomBetween(.000000008, .000000018);
    burstCanvasObjects.push({
      points: template.points.map(point => point.slice()),
      edges: template.edges,
      x: originX,
      y: originY,
      directionX: Math.cos(angle),
      directionY: Math.sin(angle),
      speed,
      minSpeed,
      deceleration,
      jerk,
      size: randomBetween(7, 14),
      born: now,
      rotation: randomBetween(0, Math.PI * 2),
      rotationSpeed: randomBetween(-.00016, .00016),
      color: Math.random() > .5 ? [82,202,255] : [168,130,255],
      opacity: randomBetween(.52, .68),
      burst: true
    });
  }
};
let coreRotationEnergy = 0;
const coreShellPointStates = Array.from({ length: 28 }, (_, index) => ({
  angle: index / 28 * Math.PI * 2,
  speed: randomBetween(.11, .43),
  distance: randomBetween(30, 135),
  size: randomBetween(1.1, 2.8)
}));
let coreNextCenterScale = .42;
const coreShapeNoise = Array.from({ length: 18 }, () => randomBetween(-1, 1));
const coreShapeTarget = coreShapeNoise.slice();
let coreShapeTargetAt = 0;
let coreInnerBlastProgress = 0;
let coreOuterBlastProgress = 0;
let coreBlastDecayProgress = 0;
let corePromotionProgress = 0;
let coreNextBirthProgress = 0;
let coreWhiteCorePhase = 0;
let corePulseStartedAt = 0;
let coreRingOneAt = 0;
let coreRingTwoAt = 0;
let coreReleaseAt = 0;
let corePulseDuration = 0;
const coreRingDuration = 950;
const coreRingFadeDuration = 7800;
const coreRingCompressionDuration = 3000;
const coreSettleDuration = 1500;
const ease = value => value * value * (3 - 2 * value);
const between = (value, from, to) => Math.min(1, Math.max(0, (value - from) / (to - from)));
const guideRadius = () => coreRingGuide ? coreRingGuide.getBoundingClientRect().width / 2 : 105;
let coreVisualPulse = 0;
let coreVisualInstability = 0;
let coreVisualNow = 0;
const updateCorePoint = (pulse = null, instability = null, now = null) => {
  if (!corePoint) return;
  if (pulse !== null) coreVisualPulse = pulse;
  if (instability !== null) coreVisualInstability = instability;
  if (now !== null) coreVisualNow = now;
  pulse = coreVisualPulse;
  instability = coreVisualInstability;
  now = coreVisualNow || performance.now();
  const innerBlast = coreInnerBlastProgress;
  const outerBlast = coreOuterBlastProgress;
  const recovery = coreNextBirthProgress;
  corePoint.style.setProperty('--core-brightness', '1');
  corePoint.style.setProperty('--core-glow', (1 + Math.max(pulse, 0) * .28 + innerBlast * .2 + outerBlast * .3).toFixed(3));
  if (coreShell && (instability > 0 || corePulseStartedAt > 0 || coreRotationEnergy > 0)) {
    const points = Array.from({ length: 18 }, (_, index) => {
      const angle = index / 18 * Math.PI * 2;
      if (now - coreShapeTargetAt > 150) {
        coreShapeTargetAt = now;
        coreShapeTarget.forEach((_, targetIndex) => { coreShapeTarget[targetIndex] = randomBetween(-1.45, 1.45); });
      }
      coreShapeNoise[index] += (coreShapeTarget[index] - coreShapeNoise[index]) * .13;
      const radius = 50 + coreShapeNoise[index] * instability * 18;
      return `${(50 + Math.cos(angle) * radius).toFixed(2)}% ${(50 + Math.sin(angle) * radius).toFixed(2)}%`;
    });
    coreShell.style.clipPath = `polygon(${points.join(',')})`;
  } else if (corePulseStartedAt > 0) {
    coreShell.style.clipPath = 'polygon(50% 0%, 67% 5%, 88% 22%, 100% 50%, 88% 78%, 67% 95%, 50% 100%, 33% 95%, 12% 78%, 0% 50%, 12% 22%, 33% 5%)';
  }
  const blastGlow = ease(between(outerBlast, .04, .72));
  const glowDecay = 1 - ease(between(coreBlastDecayProgress, .18, 1));
  if (coreBlastRed) {
    const guideInnerDiameter = Math.max(42, guideRadius() * 1.76);
    coreBlastRed.style.width = `${guideInnerDiameter.toFixed(1)}px`;
    coreBlastRed.style.height = `${guideInnerDiameter.toFixed(1)}px`;
    coreBlastRed.style.background = 'radial-gradient(circle,rgba(255,35,93,.28) 0 8%,rgba(255,66,118,.24) 30%,rgba(255,35,93,.16) 60%,rgba(255,35,93,0) 94%)';
    coreBlastRed.style.opacity = (blastGlow * glowDecay * .72).toFixed(3);
    coreBlastRed.style.transform = 'translate(-50%,-50%)';
  }
  if (coreBlastWhite) {
    const expanding = ease(between(outerBlast, 0, .38));
    const holding = 1 - ease(between(coreBlastDecayProgress, 0, .18));
    const whiteScale = recovery > 0
      ? 1 - recovery * .79
      : .21 + expanding * .79;
    const pinkAmount = recovery;
    const innerGreen = Math.round(255 - pinkAmount * 63);
    const innerBlue = Math.round(255 - pinkAmount * 45);
    const middleGreen = Math.round(238 - pinkAmount * 91);
    const middleBlue = Math.round(242 - pinkAmount * 66);
    coreBlastWhite.style.width = '24px';
    coreBlastWhite.style.height = '24px';
    coreBlastWhite.style.background = `radial-gradient(circle,rgba(255,${innerGreen},${innerBlue},.98) 0 15%,rgba(255,${middleGreen},${middleBlue},.66) 49%,rgba(255,112,164,0) 100%)`;
    coreBlastWhite.style.opacity = outerBlast > 0
      ? (holding * (1 - recovery)).toFixed(3)
      : '0';
    coreBlastWhite.style.transform = `translate(-50%,-50%) scale(${whiteScale.toFixed(3)})`;
  }
  if (coreCenter) {
    coreCenter.style.opacity = outerBlast > .01
      ? (1 - ease(between(outerBlast, 0, .28))).toFixed(3)
      : '1';
    coreCenter.style.transform = 'translate(-50%,-50%)';
  }
  if (coreNextCenter) {
    coreNextCenter.style.opacity = corePulseStartedAt && outerBlast > 0
      ? recovery.toFixed(3)
      : '0';
    coreNextCenter.style.transform = 'translate(-50%,-50%)';
  }
  if (coreShell) {
    const shellFade = ease(between(outerBlast, .08, .82));
    const shellRecovery = corePulseStartedAt && outerBlast > 0
      ? .38 + recovery * .62
      : 1;
    coreShell.style.background = 'rgb(255,137,177)';
    coreShell.style.opacity = (shellRecovery * (1 - shellFade * .92)).toFixed(3);
    coreShell.style.transform = 'translate(-50%,-50%) scale(1)';
  }
  if (coreShellPoints) {
    const shellPointFade = 1 - ease(between(outerBlast, .6, 1));
    coreShellPoints.innerHTML = outerBlast
      ? coreShellPointStates.map(point => {
          const travel = ease(between(outerBlast, 0, point.speed)) * point.distance;
          const x = Math.cos(point.angle) * travel;
          const y = Math.sin(point.angle) * travel;
          return `<i style="--x:${x.toFixed(1)}px;--y:${y.toFixed(1)}px;--s:${point.size.toFixed(1)}px;opacity:${shellPointFade.toFixed(3)}"></i>`;
        }).join('')
      : '';
  }
  coreWaves.forEach((wave, index) => {
    const ringNow = now || performance.now();
    const pulseAge = corePulseStartedAt ? ringNow - corePulseStartedAt : -1;
    const ringStart = index === 0 ? coreRingOneAt : coreRingTwoAt;
    const compression = Math.min(1, Math.max(0, (pulseAge - ringStart) / coreRingCompressionDuration));
    const compressed = ease(compression);
    const compressedRadius = index ? 27 : 12;
    let radius = 42 - compressed * (42 - compressedRadius);
    let opacity = (.78 - index * .12) * compressed;
    if (coreReleaseAt && pulseAge >= coreReleaseAt) {
      const releaseAge = pulseAge - coreReleaseAt;
      const expansion = Math.max(.0001, releaseAge / coreRingDuration);
      const fade = 1 - between(releaseAge, coreRingDuration, coreRingDuration + coreRingFadeDuration);
      radius = compressedRadius + expansion * (158 + index * 35);
      const edgeStart = guideRadius() * 2.45;
      const boundaryFade = 1 - ease(between(radius, edgeStart, edgeStart + 150));
      opacity = (.82 - index * .12) * fade * boundaryFade;
    }
    wave.style.width = `${(radius * 2).toFixed(2)}px`;
    wave.style.height = `${(radius * 2).toFixed(2)}px`;
    wave.style.opacity = opacity.toFixed(3);
    wave.style.transform = 'translate(-50%,-50%)';
  });
  if (coreScan) {
    const scanBlast = coreOuterBlastProgress;
    const scanDecay = 1 - ease(between(coreBlastDecayProgress, .1, 1));
    coreScan.style.transform = `scaleX(${(1 + scanBlast * 1.25 * scanDecay).toFixed(3)}) scaleY(${(1 + scanBlast * 3.2 * scanDecay).toFixed(3)})`;
    coreScan.style.opacity = (.42 + scanBlast * .58 * scanDecay).toFixed(3);
    coreScan.style.filter = `brightness(${(1 + scanBlast * 2.5 * scanDecay).toFixed(2)})`;
  }
};
updateCorePoint();
if (coreRectangle) {
  const pause = 1100;
  const rotateCoreRectangle = () => {
    const turns = 4 + Math.floor(Math.random() * 5);
    const direction = Math.random() > .5 ? 1 : -1;
    const duration = 7800 + Math.random() * 1800;
    const start = performance.now();
    const from = Number(coreRectangle.dataset.angle || 45);
    const to = from + direction * turns * 360;
    const tick = now => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = progress * progress * (3 - 2 * progress);
      coreRotationEnergy = Math.sin(progress * Math.PI);
      coreRectangle.style.transform = `rotate(${from + (to - from) * eased}deg)`;
      updateCorePoint();
      if (progress < 1) requestAnimationFrame(tick);
      else { coreRectangle.dataset.angle = String(to); coreRotationEnergy = 0; updateCorePoint(); window.setTimeout(rotateCoreRectangle, pause); }
    };
    requestAnimationFrame(tick);
  };
  window.setTimeout(rotateCoreRectangle, pause);
}
if (corePoint) {
  const triggerCorePulse = () => {
    burstSpawned = false;
    coreNextCenterScale = .78;
    corePulseStartedAt = performance.now();
    coreRingOneAt = 2000;
    const firstCompressionEnd = coreRingOneAt + coreRingCompressionDuration;
    const firstDipEnd = firstCompressionEnd + 480;
    const firstPeakAt = firstDipEnd + 5000 + Math.random() * 2000;
    coreRingTwoAt = firstPeakAt;
    const secondCompressionEnd = coreRingTwoAt + coreRingCompressionDuration;
    const secondDipEnd = secondCompressionEnd + 480;
    coreReleaseAt = secondDipEnd + 7000 + Math.random() * 2000;
    corePulseDuration = coreReleaseAt + coreRingDuration + coreRingFadeDuration;
    const start = corePulseStartedAt;
    const duration = corePulseDuration;
    const tick = now => {
      const age = now - start;
      const t = Math.min(1, age / duration);
      let pulse = 0;
      let instability = 0;
      coreInnerBlastProgress = 0;
      coreOuterBlastProgress = 0;
      coreBlastDecayProgress = 0;
      corePromotionProgress = 0;
      coreNextBirthProgress = 0;
      if (age < coreRingOneAt) {
        instability = .18 + ease(between(age, 0, coreRingOneAt)) * .92;
      } else if (age < firstDipEnd) {
        const dip = ease(between(age, firstCompressionEnd, firstDipEnd));
        instability = 1.1 - dip * .58;
        pulse = -.08 * dip;
      } else if (age < coreRingTwoAt) {
        instability = .52 + ease(between(age, firstDipEnd, coreRingTwoAt)) * 1.08;
      } else if (age < secondDipEnd) {
        const dip = ease(between(age, secondCompressionEnd, secondDipEnd));
        instability = 1.6 - dip * .7;
        pulse = -.12 * dip;
      } else if (age < coreReleaseAt) {
        instability = .9 + ease(between(age, secondDipEnd, coreReleaseAt)) * 1.35;
      } else {
        const releaseAge = age - coreReleaseAt;
        const release = ease(between(releaseAge, 0, coreRingDuration));
        coreOuterBlastProgress = ease(between(releaseAge, 0, 520));
        coreBlastDecayProgress = ease(between(releaseAge, 1150, 5000));
        coreInnerBlastProgress = ease(between(releaseAge, 0, 430));
        corePromotionProgress = ease(between(releaseAge, 260, 1150));
        coreNextBirthProgress = ease(between(releaseAge, 1150, 2350));
        if (releaseAge < 34) coreWhiteCorePhase = 0;
        coreWhiteCorePhase = Math.max(coreWhiteCorePhase, coreNextBirthProgress);
        if (!burstSpawned) {
          burstSpawned = true;
          spawnBurst(now);
        }
        instability = 2.25 * (1 - ease(between(releaseAge, 0, 960)));
        if (releaseAge < coreRingDuration) {
          pulse = -.12 + 1.14 * release;
        } else {
          const settle = Math.min(1, (releaseAge - coreRingDuration) / coreSettleDuration);
          pulse = 1.02 * (1 - settle);
        }
      }
      updateCorePoint(pulse, instability, now);
      if (t < 1) requestAnimationFrame(tick);
      else {
        coreInnerBlastProgress = 0;
      coreOuterBlastProgress = 0;
      coreBlastDecayProgress = 0;
      corePromotionProgress = 0;
      coreNextBirthProgress = 0;
      coreWhiteCorePhase = 0;
      corePulseStartedAt = 0;
        coreRingOneAt = 0;
        coreRingTwoAt = 0;
        coreReleaseAt = 0;
        corePulseDuration = 0;
        burstSpawned = false;
        updateCorePoint(0, 0, performance.now());
        window.setTimeout(triggerCorePulse, 7000 + Math.random() * 6200);
      }
    };
    requestAnimationFrame(tick);
  };
  window.setTimeout(triggerCorePulse, 7000 + Math.random() * 2400);
}