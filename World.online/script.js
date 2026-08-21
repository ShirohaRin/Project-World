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
const grid = document.querySelector('.grid-floor');
let gridFrame;

hero?.addEventListener('pointermove', (event) => {
  const bounds = hero.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  const horizontal = x / bounds.width - 0.5;
  const vertical = y / bounds.height - 0.5;

  cancelAnimationFrame(gridFrame);
  gridFrame = requestAnimationFrame(() => {
    const intensity = Math.min(1, Math.hypot(horizontal, vertical) * 1.7);
    grid?.style.setProperty('--grid-size', `${52 + intensity * 12}px`);
    grid?.style.setProperty('--grid-line-width', `${1 + intensity}px`);
    grid?.style.setProperty('--grid-line', `rgba(54, 180, 255, ${0.68 + intensity * 0.24})`);
    grid?.style.setProperty('--grid-shift-x', `${horizontal * -26}px`);
    grid?.style.setProperty('--grid-shift-y', `${vertical * -26}px`);
  });
});

hero?.addEventListener('pointerleave', () => {
  grid?.style.setProperty('--grid-size', '52px');
  grid?.style.setProperty('--grid-line-width', '1px');
  grid?.style.setProperty('--grid-line', 'rgba(112, 208, 255, .68)');
  grid?.style.setProperty('--grid-brightness', '1');
  grid?.style.setProperty('--grid-shift-x', '0px');
  grid?.style.setProperty('--grid-shift-y', '0px');
});

document.querySelectorAll('.filter').forEach((filter) => {
  filter.addEventListener('click', () => {
    document.querySelector('.filter.active')?.classList.remove('active');
    filter.classList.add('active');
  });
});
