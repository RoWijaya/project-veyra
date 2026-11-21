document.addEventListener("DOMContentLoaded", () => {
  const tracking = document.querySelector("[data-order-tracking]");
  if (tracking && window.WebSocket) {
    const seenEvents = new Set();
    let retryDelay = 1000;
    let retryTimer;
    let socket;
    let stopped = false;
    const connectionStatus = tracking.querySelector("[data-order-connection]");
    const connect = () => {
      if (stopped) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/guest/orders/${tracking.dataset.orderId}/`);
      socket.addEventListener("open", () => {
        retryDelay = 1000;
        if (connectionStatus) connectionStatus.textContent = "Live updates connected";
      });
      socket.addEventListener("message", (message) => {
        try {
          const event = JSON.parse(message.data);
          if (event.event_id && event.event !== "snapshot") {
            if (seenEvents.has(event.event_id)) return;
            seenEvents.add(event.event_id);
          }
          const orderStatus = tracking.querySelector("[data-order-status]");
          const paymentStatus = tracking.querySelector("[data-payment-status]");
          const paymentMethod = tracking.querySelector("[data-payment-method]");
          const orderLabels = { new: "New", preparing: "Preparing", ready: "Ready", completed: "Completed", cancelled: "Cancelled" };
          const paymentLabels = { unpaid: "Unpaid", pending: "Pending", paid: "Paid", failed: "Failed", expired: "Expired" };
          if (orderStatus && orderLabels[event.status]) orderStatus.textContent = orderLabels[event.status];
          if (paymentStatus && paymentLabels[event.payment_status]) paymentStatus.textContent = paymentLabels[event.payment_status];
          const paymentMethods = { counter: "Pay at counter", qris: "QRIS", cash: "Cash", card: "Card" };
          if (paymentMethod && paymentMethods[event.payment_method]) paymentMethod.textContent = paymentMethods[event.payment_method];
        } catch (error) {
          // Ignore malformed frames; the server-rendered order remains visible.
        }
      });
      socket.addEventListener("close", (event) => {
        if (stopped) return;
        if (event.code === 4403) {
          if (connectionStatus) connectionStatus.textContent = "Live updates unavailable";
          return;
        }
        if (connectionStatus) connectionStatus.textContent = "Reconnecting to live updates…";
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 20000);
      });
    };
    connect();
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    }, { once: true });
  }

  const inventory = document.querySelector("[data-inventory-stream]");
  if (inventory && window.WebSocket) {
    let socket;
    let stopped = false;
    let retryDelay = 1000;
    let retryTimer;
    const connectInventory = () => {
      if (stopped) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/business/${encodeURIComponent(inventory.dataset.inventoryStream)}/inventory/`);
      socket.addEventListener("open", () => { retryDelay = 1000; });
      socket.addEventListener("message", (message) => {
        try {
          const event = JSON.parse(message.data);
          if (event.event !== "inventory.update") return;
          const balance = inventory.querySelector(`[data-inventory-balance][data-item-id="${CSS.escape(event.item_id)}"][data-location-id="${CSS.escape(event.location_id)}"]`);
          const cost = inventory.querySelector(`[data-inventory-cost][data-item-id="${CSS.escape(event.item_id)}"][data-location-id="${CSS.escape(event.location_id)}"]`);
          if (cost && event.last_purchase_unit_cost != null) {
            cost.hidden = false;
            cost.querySelector("span").textContent = Number(event.last_purchase_unit_cost).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          }
          if (balance) balance.textContent = `${Number(event.quantity).toLocaleString("id-ID", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} ${balance.dataset.unit}`;
        } catch (error) { /* Ignore malformed frames. */ }
      });
      socket.addEventListener("close", (event) => {
        if (stopped || event.code === 4403) return;
        retryTimer = window.setTimeout(connectInventory, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 20000);
      });
    };
    connectInventory();
    window.addEventListener("pagehide", () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    }, { once: true });
  }

  document.querySelectorAll("[data-payment-record]").forEach((form) => {
    const method = form.querySelector("[data-tender-method]");
    const cashField = form.querySelector("[data-cash-field]");
    const amount = cashField?.querySelector("input");
    const syncTenderFields = () => {
      const isCash = method?.value === "cash";
      if (cashField) cashField.hidden = !isCash;
      if (amount) amount.required = isCash;
    };
    method?.addEventListener("change", syncTenderFields);
    syncTenderFields();
  });

  const cart = document.querySelector("[data-order-cart]");
  if (cart && !cart.matches("[data-pos-cart]")) {
    const quantities = cart.querySelectorAll("[data-cart-quantity]");
    const count = cart.querySelector("[data-cart-count]");
    const total = cart.querySelector("[data-cart-total]");
    const currency = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
    const updateCart = () => {
      let itemCount = 0;
      let amount = 0;
      quantities.forEach((input) => {
        const quantity = Math.max(0, Math.min(99, Number.parseInt(input.value, 10) || 0));
        itemCount += quantity;
        amount += quantity * Number(input.dataset.unitPrice || 0);
      });
      if (count) count.textContent = String(itemCount);
      if (total) total.textContent = currency.format(amount);
    };
    quantities.forEach((input) => input.addEventListener("input", updateCart));
    updateCart();

    if (cart.matches("[data-guest-menu]") && window.WebSocket) {
      let socket;
      let stopped = false;
      let retryDelay = 1000;
      let retryTimer;
      const refreshMenu = async () => {
        const response = await fetch(cart.dataset.menuUrl, { headers: { Accept: "application/json" } });
        if (!response.ok) return;
        const data = await response.json();
        const container = cart.querySelector("[data-menu-products]");
        const existing = new Map(Array.from(container.querySelectorAll("[data-menu-product]"), (node) => [node.dataset.menuProduct, node]));
        data.products.forEach((product) => {
          const card = existing.get(String(product.id));
          if (!card) return;
          const name = card.querySelector("[data-product-name]");
          name.textContent = product.name;
          if (product.promotions.length) {
            const badge = document.createElement("small");
            badge.className = "promotion-badge";
            badge.textContent = ` · ${product.promotions.join(" · ")}`;
            name.append(badge);
          }
          const price = card.querySelector("[data-product-price]");
          price.replaceChildren();
          if (Number(product.base_price) !== Number(product.price)) {
            const original = document.createElement("s");
            original.textContent = `Rp ${currency.format(Number(product.base_price))} `;
            price.append(original);
          }
          price.append(`Rp ${currency.format(Number(product.price))}`);
          const quantity = card.querySelector("[data-cart-quantity]");
          quantity.dataset.unitPrice = product.price;
          container.append(card);
        });
        updateCart();
      };
      const connectMenu = () => {
        if (stopped) return;
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(`${protocol}//${window.location.host}/ws/guest/menu/${encodeURIComponent(cart.dataset.menuToken)}/`);
        socket.addEventListener("open", () => { retryDelay = 1000; });
        socket.addEventListener("message", (message) => {
          try {
            if (JSON.parse(message.data).event === "menu.update") refreshMenu();
          } catch (error) { /* Ignore malformed frames. */ }
        });
        socket.addEventListener("close", (event) => {
          if (stopped || event.code === 4403) return;
          retryTimer = window.setTimeout(connectMenu, retryDelay);
          retryDelay = Math.min(retryDelay * 2, 20000);
        });
      };
      connectMenu();
      window.addEventListener("pagehide", () => {
        stopped = true;
        window.clearTimeout(retryTimer);
        socket?.close();
      }, { once: true });
    }
  }

  const menu = document.querySelector("[data-landing-menu]");
  if (menu) {
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        event.preventDefault();
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
    menu.addEventListener("click", (event) => {
      if (!event.target.closest("nav a")) return;
      menu.open = false;
      const target = event.target.closest("a");
      if (!target.hash) return;
      const section = document.querySelector(target.hash);
      if (!section) return;
      section.tabIndex = -1;
      section.focus({ preventScroll: true });
    });
  }

  const hero = document.querySelector("[data-landing-hero]");
  if (!hero || !window.gsap) return;
  const gsap = window.gsap;
  const ScrollTrigger = window.ScrollTrigger;
  const media = gsap.matchMedia();
  if (ScrollTrigger) gsap.registerPlugin(ScrollTrigger);
  media.add("(prefers-reduced-motion: no-preference)", () => {
    gsap.from(hero.querySelectorAll(".landing-hero-heading > *, .landing-hero-detail > *"), {
      y: 12, opacity: .35, duration: .5, stagger: .05, ease: "power2.out",
    });
    if (!ScrollTrigger) return;
    document.querySelectorAll(".reveal").forEach((section) => {
      gsap.from(section, {
        y: 12, opacity: .7, duration: .5, ease: "power2.out",
        scrollTrigger: { trigger: section, start: "top 93%", once: true },
      });
    });
  });

  const story = document.querySelector("[data-cinematic-story]");
  if (story && ScrollTrigger) {
    media.add("(min-width: 900px) and (min-height: 660px) and (prefers-reduced-motion: no-preference)", () => {
      const stage = story.querySelector("[data-story-stage]");
      const chapters = Array.from(story.querySelectorAll("[data-story-chapter]"));
      const progress = story.querySelector("[data-story-progress]");
      const film = story.querySelector("[data-story-film]");
      const filmStatus = story.querySelector("[data-film-status]");
      if (!stage || chapters.length < 2) return;

      const startTime = 0.35;
      let latestProgress = 0;
      const seekFilm = (progress) => {
        latestProgress = Math.min(1, Math.max(0, progress));
        if (!film || film.readyState < 2 || film.seeking || !Number.isFinite(film.duration)) return;
        const time = startTime + (film.duration - startTime) * latestProgress;
        if (Math.abs(film.currentTime - time) > 0.04) film.currentTime = time;
      };
      story.classList.add("cinema-enhanced");
      const segment = 0.92 / (chapters.length - 1);
      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: story,
          start: "top top",
          end: "bottom bottom",
          pin: stage,
          scrub: true,
          anticipatePin: 1,
          invalidateOnRefresh: true,
        },
      });
      gsap.set(chapters, { opacity: 0, y: 16 });
      gsap.set(chapters[0], { opacity: 1, y: 0 });
      chapters.forEach((chapter, index) => {
        const position = index * segment;
        if (index > 0) {
          timeline.fromTo(chapter, { opacity: 0, y: 16 }, {
            opacity: 1, y: 0, duration: 0.055, ease: "power3.out",
          }, position);
        }
        if (index < chapters.length - 1) {
          timeline.to(chapter, { opacity: 0, y: -16, duration: 0.055, ease: "power2.in" }, position + segment - 0.055);
        }
      });

      if (progress) timeline.to(progress, { scaleX: 1, ease: "none", duration: 1 }, 0);

      let observer;
      let videoTrigger;
      const controller = new AbortController();
      if (film && !navigator.connection?.saveData) {
        const loadFilm = () => {
          film.dataset.state = "loading";
          if (filmStatus) filmStatus.textContent = "Loading the restaurant film.";
          film.querySelectorAll("source[data-src]").forEach((source) => {
            source.src = source.dataset.src;
          });
          film.addEventListener("loadedmetadata", () => {
            if (Number.isFinite(film.duration) && film.duration > 0) {
              film.dataset.state = "metadata";
              ScrollTrigger.refresh();
            }
          }, { once: true, signal: controller.signal });
          film.addEventListener("loadeddata", () => {
            videoTrigger = ScrollTrigger.create({
              trigger: story,
              start: "top top",
              end: "bottom bottom",
              onUpdate: (trigger) => seekFilm(trigger.progress),
            });
            seekFilm(videoTrigger.progress);
            ScrollTrigger.refresh();
            seekFilm(videoTrigger.progress);
          }, {
            once: true, signal: controller.signal,
          });
          film.addEventListener("seeking", () => { film.dataset.state = "seeking"; }, {
            signal: controller.signal,
          });
          film.addEventListener("seeked", () => {
            film.dataset.state = "ready";
            if (!film.classList.contains("is-ready")) {
              film.classList.add("is-ready");
              if (filmStatus) filmStatus.textContent = "Restaurant film ready.";
            }
            seekFilm(latestProgress);
          }, { signal: controller.signal });
          film.addEventListener("waiting", () => { film.dataset.state = "buffering"; }, {
            signal: controller.signal,
          });
          film.addEventListener("error", () => {
            film.classList.remove("is-ready");
            film.dataset.state = "unavailable";
            if (filmStatus) filmStatus.textContent = "The restaurant film could not load. Showing the still image.";
          }, {
            signal: controller.signal,
          });
          film.load();
        };

        if ("IntersectionObserver" in window) {
          observer = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
              observer.disconnect();
              loadFilm();
            }
          }, { rootMargin: "180px 0px" });
          observer.observe(story);
        } else {
          loadFilm();
        }
      } else if (film && filmStatus) {
        filmStatus.textContent = "Showing the still image to save data.";
      }

      return () => {
        observer?.disconnect();
        controller.abort();
        videoTrigger?.kill();
        film?.pause();
        film?.classList.remove("is-ready");
        if (film) delete film.dataset.state;
        film?.querySelectorAll("source").forEach((source) => {
          source.removeAttribute("src");
        });
        film?.load();
        story.classList.remove("cinema-enhanced");
      };
    });
  }
});
