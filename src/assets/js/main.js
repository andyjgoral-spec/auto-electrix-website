// Auto Electrix – mobile menu, homepage slider and step-by-step forms.
document.documentElement.classList.add("js");

// Mobile menu
const toggle = document.querySelector(".menu-toggle");
const menu = document.getElementById("mobile-menu");
if (toggle && menu) {
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!open));
    toggle.setAttribute("aria-label", open ? "Open menu" : "Close menu");
    menu.hidden = open;
  });
}

// Homepage slider
const slider = document.querySelector(".slider");
if (slider) {
  const slides = [...slider.querySelectorAll(".slide")];
  const dots = slider.querySelector(".slider-dots");
  let current = 0;
  let timer;
  const show = (i) => {
    current = (i + slides.length) % slides.length;
    slides.forEach((s, n) => s.classList.toggle("is-active", n === current));
    dots.querySelectorAll("button").forEach((d, n) => d.setAttribute("aria-current", String(n === current)));
  };
  const start = () => {
    clearInterval(timer);
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      timer = setInterval(() => show(current + 1), 6000);
    }
  };
  slides.forEach((s, n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("aria-label", `Show slide ${n + 1}`);
    b.addEventListener("click", () => { show(n); start(); });
    dots.appendChild(b);
  });
  show(0);
  start();
}

// Step-by-step enquiry forms
document.querySelectorAll(".enquiry-form").forEach((form) => {
  const steps = [...form.querySelectorAll(".step")];
  const bar = form.querySelector(".progress span");
  const count = form.querySelector(".step-count");
  let current = 0;

  const show = (i) => {
    current = i;
    steps.forEach((s, n) => s.classList.toggle("is-active", n === i));
    const pct = Math.round(((i + 1) / steps.length) * 100);
    bar.style.width = pct + "%";
    count.textContent = `Step ${i + 1} of ${steps.length}`;
  };

  const valid = (step) => {
    for (const field of step.querySelectorAll("input, select, textarea")) {
      if (!field.checkValidity()) {
        field.reportValidity();
        return false;
      }
    }
    return true;
  };

  form.addEventListener("click", (e) => {
    if (e.target.matches("[data-next]") && valid(steps[current])) {
      show(current + 1);
      form.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (e.target.matches("[data-prev]")) {
      show(current - 1);
      form.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  // Netlify allows up to 8 MB per upload
  form.querySelectorAll('input[type="file"]').forEach((input) => {
    input.addEventListener("change", () => {
      const file = input.files[0];
      input.setCustomValidity(file && file.size > 8 * 1024 * 1024 ? "This file is over 8 MB. Please choose a smaller one." : "");
      input.reportValidity();
    });
  });

  show(0);
});
