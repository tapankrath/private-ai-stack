/* Right-Size Advisor engine, in-browser port of advisor/engine.py, sizing.py and tco.py.
 * Runs entirely in the visitor's browser; nothing is sent anywhere.
 * Parity with the Python engine is checked by tests/test_site_parity.py on the starter profiles.
 * Data (rules, catalog, hardware, runtimes, pricing) comes from advisor-data.js (built from config/*.yaml).
 */
(function (root) {
  "use strict";

  const VLLM_FAMILY = new Set(["vllm", "sglang", "tensorrt_llm"]);
  const NVIDIA = new Set(["nvidia", "nvidia_unified"]);
  const FP8_GPUS = new Set(["rtx_4060_ti", "rtx_4070", "rtx_4080", "rtx_4090", "rtx_5090", "rtx_6000_ada", "l40s", "h100_pcie", "dgx_spark", "dgx_station"]);
  const ALL_CAPABILITIES = ["chat", "long_context", "embeddings", "reranker", "document_parsing", "ocr", "vision", "stt", "code", "tool_calling", "multilingual", "web_research"];
  const EXCLUDE_HINTS = {
    stt: "No meetings, voice notes or audio to process.",
    code: "Coding is not a primary job; the general model handles occasional code questions.",
    embeddings: "No document search or conversation recall job; keyword search covers history.",
    reranker: "Only needed for searching your own documents.",
    ocr: "No scans or images to read.",
    document_parsing: "No documents to ingest.",
    long_context: "Standard context (about 8K tokens) covers this use.",
    multilingual: "All declared languages are English.",
    tool_calling: "No agent tasks or agents.",
  };
  const SENS = ["Public", "Personal", "Confidential", "Restricted"];

  /* Python's round(): ties to even on exact binary halves. */
  function pyRound(x, nd) {
    nd = nd || 0;
    const f = Math.pow(10, nd), m = x * f, fl = Math.floor(m);
    let r;
    if (m - fl === 0.5) r = fl % 2 === 0 ? fl : fl + 1;
    else r = Math.round(m);
    return r / f;
  }
  const sum = (a) => a.reduce((s, x) => s + x, 0);
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  const stableSortDesc = (arr, key) => arr.map((v, i) => [v, i]).sort((a, b) => key(b[0]) - key(a[0]) || a[1] - b[1]).map((x) => x[0]);
  const minBy = (arr, key) => arr.reduce((best, x) => (best === null || key(x) < key(best) ? x : best), null);

  // ------------------------------------------------------------------ profile normalization
  const PLATFORM_MAP = { nvidia_dgx_spark: "nvidia_unified", amd_ryzen_ai_max: "amd_unified", apple_silicon: "apple" };

  function normalizeHardware(o) {
    if (!o || !o.platform) return { known: false, platform: "cpu", hw_key: null, ram_gb: 0, vram_gb: 0, gpu_count: 1, bandwidth_gbps: null, tflops_fp16: null, free_disk_gb: null, usable_gb: null, source: "procurement" };
    const gpu = o.gpu || "none";
    let p;
    if (PLATFORM_MAP[o.platform]) p = PLATFORM_MAP[o.platform];
    else if (gpu === "nvidia") p = "nvidia";
    else if (gpu === "amd") p = "amd";
    else p = "cpu";
    return {
      known: true, platform: p, hw_key: (p === "nvidia" || p === "amd") ? (o.gpu_model || null) : (o.chip || null),
      ram_gb: +(o.ram_gb || 0), vram_gb: +(o.vram_gb || 0), gpu_count: +(o.gpu_count || 1),
      bandwidth_gbps: o.bandwidth_gbps || null, tflops_fp16: o.tflops_fp16 || null,
      free_disk_gb: o.free_disk_gb == null ? null : o.free_disk_gb, usable_gb: null, source: "profile",
      purchase_price_usd: o.purchase_price_usd == null ? null : o.purchase_price_usd,
    };
  }

  function normalizeProfile(raw) {
    const d = raw.data || {}, sens = raw.sensitivity || {}, u = raw.usage || {}, f = raw.features || {}, wl = raw.workload || {}, ag = wl.agents || {}, c = raw.customization || {}, lic = raw.licensing || {}, hw = raw.hardware || {};
    const item = (x) => ({ requested: !!(x && x.requested), justification: (x && x.justification) || "", admin_approved: !!(x && x.admin_approved) });
    const p = {
      id: raw.profile_id, description: raw.description || "", org_type: raw.org.type, org_name: raw.org.name || "",
      skill: raw.org.technical_skill, departments: raw.org.departments || [],
      users: raw.users.count, peak_concurrent: raw.users.peak_concurrent, jobs: raw.jobs.slice(),
      data_types: d.types || [], volume_gb: +(d.volume_gb || 0), growth_gb_per_month: +(d.growth_gb_per_month || 0), languages: d.languages || ["en"],
      sensitivity_level: sens.level, regulated_data: sens.regulated_data || [], air_gapped: !!sens.air_gapped,
      queries_per_user_per_day: +(u.queries_per_user_per_day == null ? 20 : u.queries_per_user_per_day), latency: u.latency || "few_seconds",
      offline_required: !!u.offline_required, context_needs: u.context_needs || "standard",
      hardware: normalizeHardware(hw.override || {}), budget: hw.budget_usd || "none",
      web_research: !!f.web_research, connectors: f.connectors || [], cloud_burst: !!f.cloud_burst, sso: !!f.sso,
      retention: raw.retention || {}, shape: wl.shape || "balanced", agents_count: +(ag.count || 0), agents_always_on: !!ag.always_on,
      customization: { post_training: item(c.post_training), distillation: item(c.distillation), abliteration: item(c.abliteration) },
      blockers: raw.blockers || [], priorities: raw.priorities || [], licensing_use: lic.use || "personal",
      licensing_scale: lic.scale || {}, acknowledged_triggers: lic.acknowledged_triggers || [], runtime_prefs: raw.runtime_preferences || {},
      assumptions: raw.assumptions || [],
    };
    p.regulated = p.regulated_data.length > 0 || p.sensitivity_level === "regulated";
    p.sessions = p.peak_concurrent + p.agents_count;
    return p;
  }

  // ------------------------------------------------------------------ sizing
  function Sizing(D) {
    const H = D.hardware, R = D.rules, Q = D.catalog.quantizations;
    function resolve(hw) {
      let key = hw.hw_key;
      if (!key || (!has(H.bandwidth_gbps, key) && !hw.bandwidth_gbps)) {
        const d = H.defaults_by_platform[hw.platform];
        if (Array.isArray(d)) { const per = hw.vram_gb / Math.max(hw.gpu_count, 1); key = d.find((x) => per <= x.max_vram_gb).hw_key; }
        else if (d) key = d.hw_key;
      }
      return Object.assign({}, hw, { hw_key: key, bandwidth_gbps: hw.bandwidth_gbps || (has(H.bandwidth_gbps, key) ? H.bandwidth_gbps[key] : null),
        tflops_fp16: hw.tflops_fp16 || (has(H.tflops_fp16, key) ? H.tflops_fp16[key] : null) });
    }
    function usable(hw) {
      const f = H.unified_usable_fraction;
      if (hw.usable_gb != null) return { gb: +hw.usable_gb, basis: `${hw.usable_gb} GB usable for models (option spec, estimate)` };
      if (hw.platform === "apple") { const fr = hw.ram_gb <= 36 ? f.apple_up_to_36gb : f.apple_above_36gb; return { gb: pyRound(hw.ram_gb * fr, 2), basis: `${Math.trunc(fr * 100)}% of ${hw.ram_gb} GB unified memory (macOS GPU limit, default)` }; }
      if (hw.platform === "nvidia_unified" || hw.platform === "amd_unified") { const fr = f[hw.platform]; return { gb: pyRound(hw.ram_gb * fr, 2), basis: `${Math.trunc(fr * 100)}% of ${hw.ram_gb} GB unified memory (OS headroom kept)` }; }
      if (hw.platform === "nvidia" || hw.platform === "amd") return { gb: +hw.vram_gb, basis: `${hw.vram_gb} GB VRAM (${hw.gpu_count > 1 ? hw.gpu_count + " x GPU" : "GPU"}); models kept fully on GPU` };
      const res = R.sizing.cpu_only_os_reserve_gb;
      return { gb: Math.max(hw.ram_gb - res, 0), basis: `${hw.ram_gb} GB RAM minus ${res} GB for the OS (CPU-only, slow)` };
    }
    function computeClass(hw) { const c = H.compute_class, tf = hw.tflops_fp16 || 0; return tf >= c.high ? "high" : tf >= c.medium ? "medium" : "low"; }
    function kvBytes(m) { const a = m.attention; return a ? 2 * a.kv_layers * a.kv_heads * a.head_dim * 2 : +m.kv_bytes_per_token_est; }
    function weights(m, q) { return m.total_params_b * Q[q] / 8; }
    function kvGb(m, ctx, sessions, mult) { const fill = sessions <= 1 ? 1 : R.sizing.multi_session_context_fill; return kvBytes(m) * ctx * sessions * fill * mult / 1e9; }
    function footprint(m, q, ctx, sessions, kv) {
      const w = weights(m, q), k = kvGb(m, ctx, sessions, R.sizing.kv_cache_types[kv]), o = D.catalog.runtime_overhead_gb;
      return { weights_gb: pyRound(w, 2), kv_cache_gb: pyRound(k, 2), overhead_gb: o, total_gb: pyRound(w + k + o, 2) };
    }
    function tps(m, q, bw) { if (!bw) return null; return pyRound(bw * R.sizing.tokens_per_sec.efficiency / (m.active_params_b * Q[q] / 8), 1); }
    function prefill(m, tf) { if (!tf) return null; return pyRound(tf * 1e12 * R.sizing.prefill_efficiency / (2 * m.active_params_b * 1e9), 0); }
    return { resolve, usable, computeClass, footprint, tps, prefill };
  }

  // ------------------------------------------------------------------ engine
  function Engine(D) {
    const R = D.rules, models = D.catalog.models, S = Sizing(D), engines = D.runtimes.engines;
    const model = (id) => models.find((m) => m.id === id);

    function deriveCapabilities(p) {
      const enabled = { chat: ["every setup needs one general model to answer questions"] };
      const add = (cap, why) => { (enabled[cap] = enabled[cap] || []); if (!enabled[cap].includes(why)) enabled[cap].push(why); };
      for (const job of p.jobs) for (const cap of R.jobs[job]) add(cap, `job '${job}'`);
      if (p.jobs.includes("knowledge_recall")) for (const t of p.data_types) for (const cap of R.data_types[t]) add(cap, `data type '${t}'`);
      if (R.long_context_needs.includes(p.context_needs)) add("long_context", `context_needs '${p.context_needs}'`);
      const langs = p.languages.filter((l) => !l.toLowerCase().startsWith("en"));
      if (langs.length) add("multilingual", `languages ${JSON.stringify(langs)}`);
      if (p.agents_count) add("tool_calling", `${p.agents_count} agent(s)`);
      const excluded = {};
      if (p.web_research) { if (p.air_gapped) excluded.web_research = "Air-gapped: no outbound traffic is allowed, so private web search is off."; else add("web_research", "features.web_research: true"); }
      else excluded.web_research = "Web research is off (features.web_research: false).";
      if (!has(enabled, "vision")) excluded.vision = !has(enabled, "ocr") ? "No job needs to understand photos or charts." : "Reading text from scans and images is enough (OCR); no photo or diagram questions selected.";
      for (const cap of ALL_CAPABILITIES) if (!has(enabled, cap) && !has(excluded, cap)) excluded[cap] = EXCLUDE_HINTS[cap] || "No selected job or data type requires it.";
      return [enabled, excluded];
    }

    function chooseTier(p) { for (const t of Object.keys(R.tiers).map(Number).sort((a, b) => a - b)) if (p.users <= R.tiers[t].max_users) return t; return 3; }

    function chooseRuntime(p, hw) {
      const notes = [];
      const pref = p.runtime_prefs.preferred;
      if (pref) {
        const e = engines[pref];
        let reason = null;
        if (!e) reason = "unknown engine";
        else if (e.never_recommend) reason = `${e.name} is in maintenance mode and is never recommended`;
        else if (e.requires === "allow_experimental" && !p.runtime_prefs.allow_experimental) reason = `${e.name} is experimental; set runtime_preferences.allow_experimental: true to use it`;
        else if (e.requires === "accept_closed_source" && !p.runtime_prefs.accept_closed_source) reason = `${e.name} is free but closed source; set runtime_preferences.accept_closed_source: true to use it`;
        else if (!e.platforms.includes(hw.platform)) reason = `${e.name} does not support this platform`;
        if (reason) notes.push(`Preferred runtime '${pref}' not used: ${reason}.`);
        else {
          if (e.open_source === false) notes.push(`${e.name} is closed source (accepted in the profile); this departs from the open-source principle.`);
          if (e.status === "experimental") notes.push(`${e.name} is EXPERIMENTAL: speedups are self-reported. Keep a fallback runtime.`);
          return { engine: pref, name: e.name, mode: hw.platform === "apple" ? "native" : "container", alternatives: [], why: "Chosen in runtime_preferences.", notes };
        }
      }
      for (const rule of D.runtimes.rules) {
        const w = rule.when;
        if (w.platform && !w.platform.includes(hw.platform)) continue;
        if (w.min_sessions && p.sessions < w.min_sessions) continue;
        if (w.skill && !w.skill.includes(p.skill)) continue;
        const eng = rule.choose;
        if (p.sessions >= 8 && !VLLM_FAMILY.has(eng)) notes.push(`${p.sessions} concurrent sessions would normally use vLLM, which needs NVIDIA; using Ollama with parallel requests instead. Expect queueing at peak.`);
        return { engine: eng, name: engines[eng].name, mode: rule.mode || (hw.platform === "apple" ? "native" : "container"), alternatives: (rule.alternatives || []).map((a) => engines[a].name), why: rule.why, notes };
      }
      throw new Error("no runtime rule matched");
    }

    function triggerApplies(t, p) {
      if (p.licensing_use !== "commercial") return false;
      const s = p.licensing_scale, typ = t.type;
      if (["commercial_use", "attribution", "use_policy"].includes(typ)) return true;
      if (typ === "mau_over") return has(s, "monthly_active_users") ? s.monthly_active_users >= t.threshold : null;
      if (typ === "mau_or_monthly_revenue_over") { if (!has(s, "monthly_active_users") && !has(s, "monthly_revenue_usd")) return null; return (s.monthly_active_users || 0) >= t.threshold_mau || (s.monthly_revenue_usd || 0) >= t.threshold_monthly_revenue_usd; }
      if (typ === "maas_revenue_over") { if (!s.offers_model_as_service) return false; return has(s, "annual_revenue_usd") ? s.annual_revenue_usd >= t.threshold : null; }
      return null;
    }
    function licenseOk(m, p) {
      if (p.licensing_use === "personal") return true;
      if (m.commercial_use === "no") return false;
      for (const t of m.commercial_triggers || []) if (t.blocking && triggerApplies(t, p) && !p.acknowledged_triggers.includes(t.id)) return false;
      return true;
    }
    function licenseNotes(sel, p) {
      return sel.map((s) => {
        const m = model(s.id), triggers = [];
        for (const t of m.commercial_triggers || []) {
          const a = triggerApplies(t, p);
          if (a === false) continue;
          triggers.push({ id: t.id, effect: t.effect, blocking: !!t.blocking, status: p.acknowledged_triggers.includes(t.id) ? "acknowledged" : (a ? "applies" : "check: depends on your scale") });
        }
        return { model: m.name, license: m.license, open_weight: m.open_weight !== false, osi_open_source: !!m.osi_open_source, commercial_use: m.commercial_use, conditions: m.license_conditions || null, triggers, last_verified: m.last_verified || null };
      });
    }

    function quants(m, engine, hw) {
      const rt = m.runtimes || {};
      let prefs;
      if (VLLM_FAMILY.has(engine)) {
        if (!has(rt, "vllm")) return [];
        prefs = R.sizing.vllm_quant_preference.slice();
        if (!FP8_GPUS.has(hw.hw_key)) prefs = prefs.filter((q) => q !== "FP8");
      } else {
        if (engine === "ollama" && !has(rt, "ollama")) return [];
        if (String(rt.ollama || "").includes("mlx") && hw.platform !== "apple") return [];
        prefs = R.sizing.ollama_quant_preference;
      }
      return prefs.filter((q) => m.quantizations.includes(q));
    }
    function pickAux(role, p, usableGb, multilingual) {
      const c = models.filter((m) => m.role === role && licenseOk(m, p));
      if (!c.length) return null;
      if (role === "stt") { const large = usableGb >= R.sizing.stt_large_min_usable_gb; return large ? stableSortDesc(c, (m) => m.memory_gb)[0] : c.map((v, i) => [v, i]).sort((a, b) => a[0].memory_gb - b[0].memory_gb || a[1] - b[1])[0][0]; }
      if (multilingual) { const mu = c.filter((m) => m.capabilities.includes("multilingual")); if (mu.length) return minBy(mu, (m) => m.memory_gb); }
      return minBy(c, (m) => m.memory_gb);
    }
    function contextOptions(target, m) { const floor = R.sizing.min_context_tokens, top = Math.min(target, m.context_max || target), o = [top]; while (Math.floor(o[o.length - 1] / 2) >= floor) o.push(Math.floor(o[o.length - 1] / 2)); return o; }
    function rank(m, p) {
      const s = R.sizing, w = s.moe_bonus_when;
      const fast = w.latency.includes(p.latency) || w.shape.includes(p.shape) || (w.agents_always_on && p.agents_always_on);
      return m.quality_rank + (fast && m.architecture === "moe" ? s.moe_rank_bonus : 0);
    }
    function pullRef(m, engine) { const rt = m.runtimes || {}; if (VLLM_FAMILY.has(engine) && rt.vllm) return rt.vllm; if (engine === "ollama" && rt.ollama) return rt.ollama; if (rt.whisper_cpp) return rt.whisper_cpp; return rt.hf || m.id; }

    function fitLlm(cands, o) {
      const rejected = [];
      for (const m of stableSortDesc(cands, (x) => rank(x, o.p))) {
        const qs = quants(m, o.engine, o.hw);
        if (!qs.length) continue;
        let tooSlow = false;
        for (const ctx of contextOptions(o.target_ctx, m)) for (const kv of Object.keys(R.sizing.kv_cache_types)) for (const q of qs) {
          const fp = S.footprint(m, q, ctx, o.sessions, kv);
          if (fp.total_gb > o.budget_left) continue;
          const t = S.tps(m, q, o.hw.bandwidth_gbps);
          if (o.tps_target && t !== null && t < o.tps_target) { tooSlow = true; continue; }
          return [{ id: m.id, name: m.name, role: m.role, quant: q, architecture: m.architecture, total_params_b: m.total_params_b, active_params_b: m.active_params_b,
            context_tokens: ctx, kv_cache_type: kv, sessions: o.sessions, memory: fp, tokens_per_sec_est: t, prefill_tokens_per_sec_est: S.prefill(m, o.hw.tflops_fp16),
            capabilities: m.capabilities, license: m.license, commercial_use: m.commercial_use, pull: pullRef(m, o.engine) }, rejected];
        }
        rejected.push(`${m.name}: ${tooSlow ? "too slow for the latency target" : "does not fit in memory"}`);
      }
      return [null, rejected];
    }

    function coversSpecialists(plan, enabled) {
      const roles = new Set(plan.specialists.map((m) => m.role)), g = plan.general || {};
      const needCode = has(enabled, "code") && !roles.has("code");
      const needVision = has(enabled, "vision") && !roles.has("vision") && !(g.covers || []).includes("vision");
      return !!plan.general && !needCode && !needVision;
    }
    function planForHardware(p, hw, enabled, tier) {
      const plan = _plan(p, hw, enabled, tier);
      if (VLLM_FAMILY.has(plan.runtime) && !coversSpecialists(plan, enabled)) {
        const alt = _plan(p, hw, enabled, tier, "ollama");
        if (coversSpecialists(alt, enabled) && alt.acceptable) { alt.notes.unshift("vLLM would not leave room for the required specialist model, so this plan uses Ollama (fewer parallel sessions; the rest queue)."); return alt; }
      }
      return plan;
    }
    function _plan(p, hw0, enabled, tier, force) {
      const s = R.sizing, hw = S.resolve(hw0);
      let rt = chooseRuntime(p, hw);
      if (force) { const e = engines[force]; rt = { engine: force, name: e.name, mode: hw.platform === "apple" ? "native" : "container", alternatives: [], why: "Fallback so every required model fits.", notes: [] }; }
      const engine = rt.engine, usable = S.usable(hw), budget = usable.gb * (1 - s.headroom_fraction), tpsTarget = s.tokens_per_sec.targets[p.latency];
      const multilingual = has(enabled, "multilingual"), notes = rt.notes.slice();
      if (hw.bandwidth_gbps == null) notes.push("Memory bandwidth unknown, so speed was not checked. Set hardware.override.bandwidth_gbps.");
      if (hw.platform === "cpu") notes.push("CPU-only: expect slow responses. A GPU or unified-memory machine is strongly preferred.");
      if (!(p.hardware.hw_key || p.hardware.bandwidth_gbps) && (hw.source === "profile" || hw.source === "detected")) notes.push(`Exact chip/GPU not given; speed assumes a conservative ${hw.hw_key}.`);
      const aux = [];
      for (const [cap, role] of [["embeddings", "embeddings"], ["reranker", "reranker"], ["stt", "stt"]]) {
        if (!has(enabled, cap)) continue;
        const m = pickAux(role, p, usable.gb, multilingual);
        if (m) aux.push({ id: m.id, name: m.name, role, memory_gb: m.memory_gb, license: m.license, commercial_use: m.commercial_use, pull: pullRef(m, "ollama"), resident: true });
      }
      let auxGb = sum(aux.map((a) => a.memory_gb));
      let fast = null;
      if (tier >= 3) {
        [fast] = fitLlm(models.filter((m) => m.role === "fast_small" && licenseOk(m, p)), { budget_left: budget - auxGb, sessions: 1, target_ctx: 8192, engine: "ollama", hw, p, tps_target: null });
        if (fast) { fast.resident = true; fast.note = "Small sidecar model for summaries and routing."; auxGb += fast.memory.total_gb; }
      }
      const sessions = VLLM_FAMILY.has(engine) ? p.sessions : Math.min(p.sessions, R.runtime.ollama_parallel_max);
      if (p.sessions > sessions) notes.push(`${p.sessions} sessions planned but this runtime serves ${sessions} in parallel; the rest queue.`);
      const targetCtx = R.context_tokens[p.context_needs];
      const generalCands = models.filter((m) => m.role === "general" && licenseOk(m, p));
      const minParams = R.tiers[tier].min_general_params_b;
      let [general, rejected] = fitLlm(generalCands, { budget_left: budget - auxGb, sessions, target_ctx: targetCtx, engine, hw, p, tps_target: tpsTarget });
      let codePick = null;
      const specEngine = VLLM_FAMILY.has(engine) ? "ollama" : engine;
      if (has(enabled, "code") && general) {
        const codeCands = models.filter((m) => m.role === "code" && licenseOk(m, p));
        const big = generalCands.filter((m) => m.total_params_b >= minParams);
        let best = null;
        for (const c of codeCands) for (const q of quants(c, specEngine, hw)) for (const kv of Object.keys(s.kv_cache_types)) {
          const ctx = Math.min(16384, c.context_max || 16384), fp = S.footprint(c, q, ctx, sessions, kv), t = S.tps(c, q, hw.bandwidth_gbps);
          if (fp.total_gb > budget - auxGb || (t !== null && t < tpsTarget)) continue;
          const [g] = fitLlm(big, { budget_left: budget - auxGb - fp.total_gb, sessions, target_ctx: targetCtx, engine, hw, p, tps_target: tpsTarget });
          if (!g) continue;
          const score = [c.quality_rank + model(g.id).quality_rank, -(fp.total_gb + g.memory.total_gb)];
          if (best === null || score[0] > best[0][0] || (score[0] === best[0][0] && score[1] > best[0][1])) {
            best = [score, { id: c.id, name: c.name, role: "code", quant: q, architecture: c.architecture, total_params_b: c.total_params_b, active_params_b: c.active_params_b,
              context_tokens: ctx, kv_cache_type: kv, sessions, memory: fp, tokens_per_sec_est: t, capabilities: c.capabilities, license: c.license, commercial_use: c.commercial_use, pull: pullRef(c, specEngine), resident: true }, g];
          }
        }
        if (best) { codePick = best[1]; general = best[2]; if (VLLM_FAMILY.has(engine)) codePick.note = "Served by an Ollama sidecar; vLLM serves the general model."; }
      }
      let used = auxGb + (general ? general.memory.total_gb : 0) + (codePick ? codePick.memory.total_gb : 0);
      if (general) {
        general.resident = true;
        if (general.context_tokens < targetCtx) notes.push(`Context reduced from ${targetCtx.toLocaleString("en-US")} to ${general.context_tokens.toLocaleString("en-US")} tokens to fit memory.`);
        if (general.kv_cache_type !== "f16") notes.push("KV cache quantized to q8_0 to fit memory (needs flash attention; small quality cost).");
      }
      const specialists = codePick ? [codePick] : [];
      for (const [cap, role, ctx] of [["code", "code", 16384], ["vision", "vision", 8192]]) {
        if (!has(enabled, cap) || !general || (role === "code" && codePick)) continue;
        if (role === "vision" && general.capabilities.includes("vision")) { general.covers = (general.covers || []).concat(["vision"]); continue; }
        const cands = models.filter((m) => m.role === role && licenseOk(m, p)), sess = role === "vision" ? 1 : sessions;
        let [pick] = fitLlm(cands, { budget_left: budget - used, sessions: sess, target_ctx: ctx, engine: specEngine, hw, p, tps_target: tpsTarget });
        if (pick) { pick.resident = true; used += pick.memory.total_gb; }
        else if (!VLLM_FAMILY.has(engine)) {
          [pick] = fitLlm(cands, { budget_left: budget - auxGb, sessions: sess, target_ctx: ctx, engine: specEngine, hw, p, tps_target: tpsTarget });
          if (pick) { pick.resident = false; pick.note = "Loaded on demand: swaps with the general model (a few seconds of load time)."; }
        }
        if (pick) specialists.push(pick);
        else notes.push(role === "code" ? "No code model fits; the general model handles coding." : "No vision model fits this hardware; OCR still extracts text from images.");
      }
      let ttft = null;
      if (general && general.prefill_tokens_per_sec_est) {
        const pt = D.pricing.usage_assumptions.input_tokens_per_query[p.context_needs];
        ttft = pyRound(pt / general.prefill_tokens_per_sec_est, 1);
        if (ttft > s.ttft_targets_s[p.latency]) notes.push(`Reading a typical ${pt.toLocaleString("en-US")}-token prompt takes ~${ttft}s before the answer starts (prefill, estimate).` + (p.shape === "prompt_heavy" ? " Prompt-heavy work favors higher-compute GPUs." : ""));
      }
      const acceptable = !!general && general.total_params_b >= minParams;
      if (general && !acceptable) notes.push(`Best fitting model (${general.name}) is below the ${minParams}B minimum for tier ${tier}.`);
      return { runtime: engine, runtime_name: rt.name, runtime_mode: rt.mode, runtime_alternatives: rt.alternatives, runtime_why: rt.why,
        hardware: { platform: hw.platform, hw_key: hw.hw_key, gpu_count: hw.gpu_count, source: hw.source, compute_class: S.computeClass(hw) },
        usable_memory_gb: usable.gb, usable_memory_basis: usable.basis, memory_budget_gb: pyRound(budget, 2), memory_used_gb: pyRound(used, 2),
        memory_use_pct: usable.gb ? pyRound(100 * used / usable.gb, 1) : null, bandwidth_gbps: hw.bandwidth_gbps, tflops_fp16: hw.tflops_fp16,
        tokens_per_sec_target: tpsTarget, ttft_s_est: ttft, general, specialists, aux: aux.concat(fast ? [fast] : []), rejected, acceptable, notes };
    }

    function optionHw(o) { return { known: true, platform: o.platform, hw_key: o.hw_key, usable_gb: o.usable_gb, gpu_count: o.gpu_count || 1, vram_gb: (o.platform === "nvidia" || o.platform === "amd") ? o.usable_gb : 0, ram_gb: o.usable_gb, bandwidth_gbps: null, tflops_fp16: null, free_disk_gb: null, source: o.id }; }
    function shapeScore(o, plan, p) {
      const r = R.workload_shape, sh = r[p.shape] || {};
      let score = +o.price_usd[0];
      if (sh.prefer === "compute" && plan.hardware.compute_class !== sh.penalty_unless_compute_class) score *= sh.penalty;
      if (sh.prefer === "bandwidth" && (plan.bandwidth_gbps || 0) < sh.penalty_below_bandwidth_gbps) score *= sh.penalty;
      if (p.sessions >= 8 && !NVIDIA.has(o.platform)) score *= r.vllm_needs_nvidia_penalty;
      return score;
    }
    function evaluateOptions(p, enabled, tier, cap, minRank) {
      const out = [];
      for (const o of D.pricing.hardware_options) {
        if (o.max_tier < tier) continue;
        if (cap != null && o.price_usd[1] > cap) continue;
        const plan = planForHardware(p, optionHw(o), enabled, tier);
        if (!plan.acceptable) continue;
        const g = plan.general, t = g.tokens_per_sec_est;
        if (t !== null && t < plan.tokens_per_sec_target) continue;
        const rk = model(g.id).quality_rank;
        if (minRank != null && rk < minRank) continue;
        out.push({ id: o.id, name: o.name, price_usd: o.price_usd, market_tier: o.market_tier, last_verified: o.last_verified || null, general_model: `${g.name} ${g.quant}`,
          tokens_per_sec_est: t, context_tokens: g.context_tokens, rank: rk, score: shapeScore(o, plan, p), plan, option: o });
      }
      return out.map((v, i) => [v, i]).sort((a, b) => a[0].score - b[0].score || b[0].rank - a[0].rank || a[1] - b[1]).map((x) => x[0]);
    }
    const pub = (o) => { const c = Object.assign({}, o); delete c.plan; delete c.option; delete c.score; return c; };

    function components(p, tier, plan, enabled, exc0) {
      const comp = R.components, inc = [], exc = [];
      const include = (id, name, reason, cp, alts) => inc.push({ id, name, reason, compose_profile: cp || null, alternatives: alts || [] });
      const exclude = (id, name, reason) => exc.push({ id, name, reason });
      const engine = plan.runtime, native = plan.runtime_mode === "native";
      if (VLLM_FAMILY.has(engine)) { include(engine, `${plan.runtime_name} (OpenAI-compatible)`, plan.runtime_why, "vllm", plan.runtime_alternatives); include("ollama", "Ollama sidecar", "Serves embeddings and any small or specialist models.", "ollama"); }
      else { include(engine !== "ollama" ? engine : (native ? "ollama-native" : "ollama"), `${plan.runtime_name}${native ? " (installed natively on macOS)" : ""}`, plan.runtime_why, native ? null : "ollama", plan.runtime_alternatives); exclude("vllm", "vLLM / SGLang", `Only ${p.sessions} concurrent session(s), or no NVIDIA GPU; the simpler runtime is enough.`); }
      include("open-webui", "Open WebUI", "Chat UI bound to localhost/LAN, telemetry disabled.", "ui", ["AnythingLLM", "custom thin UI on the Gateway"]);
      include("caddy", "Caddy reverse proxy", comp.reverse_proxy, "ui", ["Traefik"]);
      include("gateway", "Gateway", "The only path to the model: policy, access rules, audit log, Recall, memory and searchable history.", "ui");
      include("backups", "Encrypted backups (restic)", comp.backups, null, ["borg"]);
      include("model-pinning", "Model pinning", "Model files pinned by checksum; no auto-upgrade; a new version must pass the golden-set eval before promotion.");
      if (tier >= 2) { include("postgres", "Postgres", "Shared conversation, metadata and ACL store for multiple users.", "db"); include("accounts", "Per-user accounts", "Every person has their own login and history."); }
      else exclude("postgres", "Postgres server", "Single user: SQLite on the encrypted disk is enough.");
      if (has(enabled, "embeddings")) { const vs = comp.vector_store[tier]; include("vector-store", vs.default, "Semantic + keyword (hybrid) search over documents and history.", tier >= 2 ? "db" : null, vs.alternatives); }
      else exclude("vector-store", "Vector database", EXCLUDE_HINTS.embeddings);
      if (has(enabled, "document_parsing")) include("docling", "Docling document parser", "Parses PDFs, Office files and email exports locally.", "ingest", ["Unstructured", "Apache Tika"]); else exclude("docling", "Document parser", EXCLUDE_HINTS.document_parsing);
      if (has(enabled, "ocr")) include("ocr", "Local OCR pipeline (Tesseract via Docling)", "Scans and images need text extraction.", "ingest", ["PaddleOCR"]); else exclude("ocr", "OCR pipeline", EXCLUDE_HINTS.ocr);
      if (has(enabled, "stt")) include("whisper", "whisper.cpp server", "Meetings and voice notes need local speech-to-text.", "voice"); else exclude("whisper", "Speech-to-text (whisper.cpp)", EXCLUDE_HINTS.stt);
      if (has(enabled, "web_research")) include("searxng", "SearXNG (private web research)", "Private metasearch: searches are not tied to your identity. The only service allowed outbound, and only to search engines." + (p.regulated ? " Regulated data is present, so this is a named, audited egress exception you opted into." : ""), "research");
      else exclude("searxng", "SearXNG web research", exc0.web_research);
      if (p.sso || tier >= 3) { include("authentik", "Authentik (OIDC SSO + MFA)", "Central login with MFA" + (p.sso ? " (requested)" : " for a 20+ user tier"), "sso", ["Keycloak"]); include("rbac", "Role-based access control", "Roles from SSO groups decide which collections each user can search."); }
      else exclude("authentik", "SSO (Authentik/Keycloak)", tier === 1 ? "Single user on localhost." : "Built-in per-user accounts are enough for this group (features.sso: false).");
      if (tier >= 3) include("observability", comp.observability_tier3, "Capacity, latency, queue depth and ingestion monitoring.", "observability"); else exclude("observability", "Prometheus / Grafana / Loki", "Too heavy for this tier; the Gateway health page covers basics.");
      if (p.agents_always_on) include("agent-watchdog", "Agent watchdog", "Always-on agents: health checks, automatic restart, alerts; runs only pinned models.");
      if (p.connectors.length && !p.air_gapped) include("connectors", `Scoped read-only connectors: ${p.connectors.join(", ")}`, "Requested. OAuth with minimal read-only scopes, tokens in the vault, per-connector kill switch, 'last accessed' log. (Phase 5)");
      else exclude("connectors", "Live account connectors (email, social, banking)", "None requested. Use exported files (.mbox, notes exports) instead of account access.");
      if (p.cloud_burst && !p.air_gapped) include("cloud-burst", "Cloud burst mode (opt-in per request)", "Requested. Every request needs explicit opt-in, PII redaction, a zero-retention provider and an audit entry.");
      else exclude("cloud-burst", "Cloud burst mode", "Off by default. The advisor never turns it on.");
      if (tier === 1) exclude("gpu-server", "Dedicated GPU server", "One user on their own machine does not need a server.");
      if (tier >= 2) exclude("desktop-installs", "Per-user desktop installs", "One shared server is simpler to secure, back up and update.");
      return [inc, exc];
    }

    function tokens(p, tier, plan, enabled, inc, custom) {
      const ids = new Set(inc.map((c) => c.id)), t = new Set([`tier_${tier}`, plan.runtime]), g = plan.general;
      if (g) t.add(g.total_params_b <= 9 ? "general_chat_small" : g.total_params_b <= 40 ? "general_chat_mid" : "general_chat_large");
      const roles = {};
      for (const m of plan.specialists.concat(plan.aux)) roles[m.role] = m;
      if (roles.embeddings) t.add("embeddings");
      if (roles.reranker) { t.add("reranker"); if (roles.reranker.memory_gb >= 1.0) t.add("reranker_large"); }
      if (roles.stt) t.add("speech_to_text");
      if (roles.code) t.add("code_model");
      if (has(enabled, "vision")) { t.add("vision"); if (roles.vision && roles.vision.total_params_b <= 9) t.add("vision_small"); }
      if (roles.fast_small) t.add("fast_small_model");
      for (const c of ["multilingual", "long_context", "tool_calling"]) if (has(enabled, c)) t.add(c);
      t.add("conversation_history");
      if (has(enabled, "embeddings")) t.add("hybrid_search");
      if (p.jobs.includes("knowledge_recall") && p.data_types.includes("code")) t.add("code_aware_chunking");
      const map = { ocr: "ocr_pipeline", searxng: "searxng", authentik: "sso", rbac: "rbac", observability: "monitoring", backups: "encrypted_backups", "model-pinning": "model_pinning", "agent-watchdog": "agent_watchdog", accounts: "per_user_accounts", connectors: "live_connectors", "cloud-burst": "cloud_burst", vllm: "vllm", sglang: "vllm" };
      for (const k in map) if (ids.has(k)) t.add(map[k]);
      t.add("audit_log");
      if (tier >= 2) { t.add("collection_acls"); t.add("gpu_server"); }
      if (p.regulated) t.add("restricted_tier");
      if (p.air_gapped) t.add("air_gap_mode");
      for (const k of ["post_training", "distillation"]) if (custom[k].status === "planned") { t.add(k); t.add(`customize_track_${k}`); }
      if (custom.abliteration.status === "approved") t.add("abliteration");
      return Array.from(t).sort();
    }

    function customization(p, plan) {
      const out = {}, g = plan.general, gm = g ? model(g.id) : null, pt = p.customization.post_training;
      if (!pt.requested) out.post_training = { status: "not_requested" };
      else if (!pt.justification.trim()) out.post_training = { status: "blocked", reason: "Requested without a justification." };
      else if (gm && gm.allows_fine_tuning === "no") out.post_training = { status: "blocked", reason: `${gm.name}'s license does not allow fine-tuning.` };
      else out.post_training = { status: "planned", base_model: gm ? gm.name : null, license_check: gm ? gm.allows_fine_tuning : "unknown", gate: "RAG first. Start only when the golden-set eval shows RAG alone misses domain terms or behavior.", requires: ["dataset sensitivity review", "versioned LoRA adapters", "before/after eval on the golden set", "runs locally; weights and data never leave"], justification: pt.justification };
      const di = p.customization.distillation;
      if (!di.requested) out.distillation = { status: "not_requested" };
      else if (!di.justification.trim()) out.distillation = { status: "blocked", reason: "Requested without a justification." };
      else {
        const teachers = models.filter((m) => m.role === "general" && licenseOk(m, p) && m.allows_distillation !== "no" && m.quality_rank > (gm ? gm.quality_rank : 0));
        const teacher = teachers.reduce((b, m) => (b === null || m.quality_rank > b.quality_rank ? m : b), null);
        out.distillation = teacher ? { status: "planned", teacher: teacher.name, teacher_license: teacher.license, teacher_distillation_allowed: teacher.allows_distillation, student: gm ? gm.name : null, why: "Larger-model quality on the serving hardware / high concurrency.", note: "The teacher can run temporarily on rented or borrowed hardware you control; the student serves users.", requires: ["teacher license permits distillation", "before/after eval", "versioned promotion"], justification: di.justification } : { status: "blocked", reason: "No larger catalog model whose license allows distillation." };
      }
      const ab = p.customization.abliteration;
      if (!ab.requested) out.abliteration = { status: "not_requested" };
      else {
        const why = [];
        if (!ab.justification.trim()) why.push("no recorded justification");
        if (!ab.admin_approved) why.push("no admin approval");
        if (p.org_type === "household" || p.regulated_data.includes("children")) why.push("never for household or children's-data profiles");
        out.abliteration = why.length ? { status: "refused", reason: why.join("; ") } : { status: "approved", justification: ab.justification, guardrails: ["Gateway content filters stay on", "every request audited", "RBAC limits who can use the model", "separate model id, never the default", "documented in the threat model (ADR-007)"] };
      }
      return out;
    }

    function whyLocal(p) {
      const where = p.users === 1 ? "this computer" : "your network";
      const text = {
        privacy: `Prompts, documents and answers never leave ${where}. That is a simpler guarantee than any contract a provider can offer` + (p.regulated ? ", and it matters for the regulated data you hold." : "."),
        security: "You control the whole chain: the model, its exact version, the serving software and who can reach it. No outside service joins your attack surface.",
        availability: "No rate limits, no outages caused by someone else's demand, no surprise deprecations. A validated, pinned model behaves the same next month" + (p.agents_always_on ? ", which matters most for your always-on agents." : "."),
        customization: "Open weights let you train on your own material or distill a faster model, locally, without handing data or weights to a provider.",
      };
      const order = p.priorities.filter((x) => has(text, x)).concat(["privacy", "security", "availability"].filter((x) => !p.priorities.includes(x)));
      if (p.priorities.includes("customization") && !order.includes("customization")) order.push("customization");
      return order.map((b, i) => ({ benefit: b, weight: order.length - i, text: text[b] }));
    }

    function machinePower(hw) {
      const ref = D.hardware, sysw = ref.system_power_watts, plat = hw.platform, key = hw.hw_key || "";
      if (plat === "nvidia" || plat === "amd") {
        const n = Math.max(+(hw.gpu_count || 1), 1);
        const card = has(ref.gpu_power_watts, key) ? ref.gpu_power_watts[key] : (plat === "amd" ? ref.gpu_power_watts.amd_default : 300);
        return [sysw.pc.idle + n * ref.gpu_idle_watts_per_card, sysw.pc.load + n * card];
      }
      if (plat === "apple") { const cls = ["ultra", "max", "pro"].find((c) => key.endsWith(c)) || "base"; return [sysw.apple[cls].idle, sysw.apple[cls].load]; }
      if (has(sysw, plat)) return [sysw[plat].idle, sysw[plat].load];
      return { apple: [10, 90], nvidia: [70, 420], nvidia_unified: [30, 240], amd_unified: [15, 160], amd: [60, 350], cpu: [40, 150] }[plat] || [40, 150];
    }

    function tco(p, rec) {
      const pr = D.pricing, run = pr.running, tier = rec.tier, plan = rec.plan, MONTHS = 36;
      const POWER = { apple: [10, 90], nvidia: [70, 420], nvidia_unified: [30, 240], amd_unified: [15, 160], amd: [60, 350], cpu: [40, 150] };
      let hwCost, idle, load, share, note, opt = null;
      if (rec.procurement) opt = pr.hardware_options.find((o) => o.id === rec.procurement.id);
      else if (rec.upgrades_required && rec.upgrade_options.length) opt = pr.hardware_options.find((o) => o.id === rec.upgrade_options[0].id);
      if (opt) { hwCost = (opt.price_usd[0] + opt.price_usd[1]) / 2; idle = opt.power_watts.idle; load = opt.power_watts.load; share = 1; note = `$${opt.price_usd[0].toLocaleString("en-US")}-$${opt.price_usd[1].toLocaleString("en-US")} ${rec.procurement ? "for " + opt.name : "(cheapest upgrade that fits, estimate)"}${rec.procurement ? " (estimate)" : ""}`; }
      else {
        [idle, load] = machinePower(plan.hardware);
        const price = p.hardware.purchase_price_usd;
        if (price) { hwCost = +price; share = 1; note = `$${Math.round(price).toLocaleString("en-US")} to buy this machine (your figure)`; }
        else { hwCost = 0; share = run.existing_machine_power_share; note = "$0 new hardware (you already have this machine)"; }
      }
      const hours = run.active_hours_per_day, kwh = (load * hours + idle * (24 - hours)) / 1000 * 365 * 3 * share, power = kwh * run.electricity_usd_per_kwh;
      const rate = run.admin_rate_usd_per_hour[p.org_type], admin = run.admin_hours_per_month[tier] * rate * MONTHS, network = run.networking_one_time_usd[tier];
      const u = pr.usage_assumptions, queries = p.users * p.queries_per_user_per_day * u.working_days_per_month * MONTHS;
      const recallJob = p.jobs.includes("knowledge_recall") || p.jobs.includes("conversation_recall");
      const pq = { base: u.input_tokens_per_query[p.context_needs], history: u.history_tokens_per_query, recall: recallJob ? u.recall_tokens_per_query : 0 };
      pq.typical = pq.base + pq.history + pq.recall; pq.low = pq.base;
      const days = p.agents_always_on ? 30 * MONTHS : u.working_days_per_month * MONTHS;
      const v = { queries, human_in: queries * pq.typical, human_in_low: queries * pq.low, human_out: queries * u.output_tokens_per_query,
        agent_in: p.agents_count * u.agent_tokens_per_day.input * days, agent_out: p.agents_count * u.agent_tokens_per_day.output * days, per_query: pq };
      const cost = (price, a, b, fee) => (a / 1e6 * price.input + b / 1e6 * price.output) * (1 + (fee || 0));
      const tout = v.human_out + v.agent_out, tin = v.human_in + v.agent_in, tinLow = v.human_in_low + v.agent_in, sub = pr.subscription;
      const om = pr.api.open_model, fr = pr.api.frontier, omFee = om.router_fee_fraction || 0, frFee = fr.router_fee_fraction || 0;
      const perUser = p.queries_per_user_per_day >= sub.heavy_user_queries_per_day ? sub.heavy_user_usd_per_month : sub.usd_per_user_per_month;
      let subTotal = p.users * perUser * MONTHS, subAgents = 0;
      if (p.agents_count && !sub.covers_agents) subAgents = cost(om.usd_per_million, v.agent_in, v.agent_out, omFee);
      subTotal += subAgents;
      const pct = (f) => (Math.round(f * 1000) / 10).toFixed(1) + "%";
      const localTotal = pyRound(hwCost + power + admin + network);
      const options = {
        local: { label: "Local (this recommendation)", total_usd: localTotal, low_usd: localTotal, breakdown: { hardware: pyRound(hwCost), power: pyRound(power), admin_time: pyRound(admin), networking: network }, hardware_note: note, data_goes_to: null },
        api_open: { label: om.label, total_usd: pyRound(cost(om.usd_per_million, tin, tout, omFee)), low_usd: pyRound(cost(om.low_usd_per_million || om.usd_per_million, tinLow, tout, omFee)),
          fee_usd: pyRound(cost(om.usd_per_million, tin, tout, omFee) - cost(om.usd_per_million, tin, tout)),
          fee_note: omFee ? `includes $${pyRound(cost(om.usd_per_million, tin, tout, omFee) - cost(om.usd_per_million, tin, tout)).toLocaleString("en-US")} of router fees (${pct(omFee)})` : null, data_goes_to: om.data_goes_to || null },
        api_frontier: { label: fr.label, total_usd: pyRound(cost(fr.usd_per_million, tin, tout, frFee)), low_usd: pyRound(cost(fr.usd_per_million, tinLow, tout, frFee)),
          fee_note: frFee ? `includes a ${pct(frFee)} fee` : null, data_goes_to: fr.data_goes_to || null },
        subscription: { label: sub.label, total_usd: pyRound(subTotal), low_usd: pyRound(subTotal), note: subAgents ? `includes ~$${pyRound(subAgents).toLocaleString("en-US")} of API usage for agents (subscriptions rarely cover them)` : null, data_goes_to: sub.data_goes_to || null },
      };
      const cheapest = Object.keys(options).reduce((b, k) => (b === null || options[k].total_usd < options[b].total_usd ? k : b), null);
      const reasons = rec.why_local.map((w) => w.benefit).filter((b) => b !== "cost");
      let verdict;
      if (cheapest === "local") verdict = "On these estimates local is the cheapest of the options over three years.";
      else {
        const diff = options.local.total_usd - options[cheapest].total_usd;
        const short = { api_open: "a hosted open-weight model API", api_frontier: "a frontier model API", subscription: "a flat-rate subscription" }[cheapest];
        verdict = `On these estimates, ${short} would be cheaper than local by about $${diff.toLocaleString("en-US")} over three years. Local is recommended here for ${reasons.slice(0, 3).join(", ") || "non-price reasons"}, not price.`;
        const goes = options[cheapest].data_goes_to || "the provider";
        verdict += " The saving has a privacy cost: every question, the chat history sent with it" + (v.per_query.recall ? " and the document passages Recall finds" : "") + ` would go to ${goes}.`;
        if (p.air_gapped || p.regulated) verdict += " For this profile that is not just a preference: its " + (p.air_gapped ? "air-gap requirement rules hosted options out." : "regulated data makes it a compliance question.");
      }
      const today = new Date(), stale = [];
      const check = (name, e) => { const lv = e.last_verified; if (!lv) { stale.push(name); return; } const dd = (today - new Date(lv + "T00:00:00")) / 86400000; if (dd > pr.stale_after_days) stale.push(name); };
      check("running costs", run); check("open-model API price", pr.api.open_model); check("frontier API price", pr.api.frontier); check("subscription price", sub);
      if (opt) check(`price of ${opt.name}`, opt);
      return { months: MONTHS, options, local: options.local, cheapest, local_is_cheapest: cheapest === "local", verdict, token_volume: v, stale_or_unverified_prices: stale, admin_rate: rate };
    }

    function recommend(raw) {
      const p = normalizeProfile(raw);
      const [enabled, excludedCaps] = deriveCapabilities(p), tier = chooseTier(p), caps = D.pricing.budget_caps_usd, warnings = [];
      let plan, procurement = null, upgrades = [], gbb = [];
      if (p.hardware.known) {
        plan = planForHardware(p, p.hardware, enabled, tier);
        if (!plan.acceptable) upgrades = evaluateOptions(p, enabled, tier, null).slice(0, 3).map(pub);
        else if (p.budget !== "none") {
          const cur = model(plan.general.id).quality_rank, seen = new Set();
          for (const o of evaluateOptions(p, enabled, tier, caps[p.budget], cur + 8)) if (!seen.has(o.general_model)) { seen.add(o.general_model); gbb.push(pub(o)); }
          gbb = gbb.slice(0, 3);
        }
      } else {
        const within = evaluateOptions(p, enabled, tier, p.budget !== "none" ? caps[p.budget] : null);
        let choice;
        if (within.length && p.budget !== "none") choice = within[0];
        else {
          const all = evaluateOptions(p, enabled, tier, null);
          choice = all.length ? all[0] : null;
          if (choice && p.budget !== "none") warnings.push(`No listed option fits the ${p.budget} budget; the cheapest one that does is shown.`);
          if (choice && p.budget === "none") warnings.push("No hardware given and no budget set: showing the cheapest option that fits.");
        }
        if (choice) {
          plan = choice.plan; procurement = pub(choice);
          const seen = new Set([choice.general_model]);
          for (const o of within.slice(1)) if (!seen.has(o.general_model) && o.rank >= choice.rank + 8) { seen.add(o.general_model); gbb.push(pub(o)); }
          gbb = gbb.slice(0, 2);
        } else {
          plan = planForHardware(p, { known: true, platform: "cpu", ram_gb: 16, vram_gb: 0, gpu_count: 1, hw_key: null, bandwidth_gbps: null, tflops_fp16: null, usable_gb: null, source: "none" }, enabled, tier);
          warnings.push("No listed workstation fits: this profile has outgrown local workstations (Enterprise AI infrastructure, out of scope).");
        }
      }
      const [inc, exc] = components(p, tier, plan, enabled, excludedCaps);
      const custom = customization(p, plan);
      if (custom.abliteration.status === "refused") exc.push({ id: "abliteration", name: "Abliteration (refusal removal)", reason: `Requested but refused: ${custom.abliteration.reason}.` });
      const excludedModels = [];
      for (const [cap, label] of [["vision", "Vision-language model"], ["code", "Code model"], ["stt", "Speech-to-text model"]]) if (!has(enabled, cap)) excludedModels.push({ id: cap, name: label, reason: excludedCaps[cap] });
      if (tier < 3) excludedModels.push({ id: "fast_small", name: "Separate small routing/summary model", reason: "The general model handles summaries; saves memory at this tier." });
      for (const [k, label] of [["post_training", "Post-training on your data"], ["distillation", "Distillation to a smaller model"]]) if (custom[k].status !== "planned") excludedModels.push({ id: k, name: label, reason: custom[k].reason || "Not requested." });
      const s = R.sizing;
      let modelDisk = sum((plan.general ? [plan.general] : []).concat(plan.specialists).map((m) => m.memory.weights_gb));
      modelDisk += sum(plan.aux.map((a) => (a.memory_gb != null ? a.memory_gb : (a.memory || {}).weights_gb || 0)));
      const disk = pyRound(modelDisk + (p.volume_gb + 12 * p.growth_gb_per_month) * s.index_overhead_factor + s.base_disk_gb, 1);
      const selected = (plan.general ? [plan.general] : []).concat(plan.specialists, plan.aux);
      const lic = p.licensing_use === "commercial" ? licenseNotes(selected, p) : [];
      for (const ln of lic) if (!ln.osi_open_source) warnings.push(`${ln.model} is open-weight but not OSI open source (${ln.license}); read its terms.`);
      if (p.offline_required && has(enabled, "web_research")) warnings.push("Offline operation is required: web research works only while a connection is available.");
      for (const a of p.assumptions) warnings.push(`Assumption: ${a}`);
      const rec = {
        profile_id: p.id, tier, tier_name: R.tiers[tier].name, setup_effort: R.tiers[tier].setup_effort, latency: p.latency, skill: p.skill,
        capabilities: { enabled, excluded: excludedCaps }, plan, procurement,
        market_tier: (procurement && procurement.market_tier) || (plan.general ? "consumer_ai_workstation" : "enterprise_ai_infrastructure"),
        components: { included: inc, excluded: exc }, excluded_models: excludedModels, upgrades_required: !!(p.hardware.known && !plan.acceptable),
        upgrade_options: upgrades, good_better_best: gbb, disk_estimate_gb: disk, licenses: lic, customization: custom, why_local: whyLocal(p),
        regulated: p.regulated, air_gapped: p.air_gapped, users: p.users, warnings: warnings.concat(plan.notes),
      };
      rec.tco = tco(p, rec);
      rec.tokens = tokens(p, tier, plan, enabled, inc, custom);
      return rec;
    }
    return { recommend, normalizeProfile };
  }

  root.AdvisorEngine = { Engine, pyRound, SENS };
})(typeof globalThis !== "undefined" ? globalThis : this);
