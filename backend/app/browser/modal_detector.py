"""AI-driven modal, disclaimer, and terms detector and dismisser.

Analyzes loaded pages and frames dynamically using AI to detect any blocking disclaimer,
website notice, terms of use, or alert popup, identifies the primary dismiss/accept button,
executes the dismissal, and returns the discovered selector for saving into recipes.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from playwright.async_api import Frame, Page

from app.core.config import settings
from app.core.llm_client import chat_completions_create, llm_configured

logger = logging.getLogger(__name__)

MODAL_CANDIDATES_JS = r"""
() => {
    const candidates = [];
    const foundElements = new Set();

    // 1. Specific modal/dialog/alert container selectors across county portals
    const containerSelectors = [
        "[role='dialog']", "[role='alertdialog']", ".modal", ".ui-dialog", 
        "div[id*='alert' i]", "div[class*='alert' i]",
        "div[id*='disclaimer' i]", "div[class*='disclaimer' i]",
        "div[id*='terms' i]", "div[class*='terms' i]",
        "div[id*='modal' i]", "div[class*='modal' i]",
        "div[id*='popup' i]", "div[class*='popup' i]",
        "div[id*='overlay' i]", "div[class*='overlay' i]",
        "div[id*='notification' i]", "div[class*='notification' i]",
        "div[id*='notice' i]", "div[class*='notice' i]",
        "div[id*='consent' i]", "div[class*='consent' i]",
        "div[id*='splash' i]", "div[class*='splash' i]",
        "div[id*='banner' i]", "div[class*='banner' i]",
        "div[id*='warning' i]", "div[class*='warning' i]",
        "[data-test*='notification' i]", "[data-test*='modal' i]"
    ];

    containerSelectors.forEach(sel => {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (el !== document.body) foundElements.add(el);
            });
        } catch(e) {}
    });

    // 2. Discover any visible button/input specifically offering agreement or dismissal
    const interactiveButtons = document.querySelectorAll(
        "input[type='button'], input[type='submit'], button, a[role='button'], a.btn, a"
    );
    interactiveButtons.forEach(btn => {
        try {
            const txt = (btn.innerText || btn.value || btn.getAttribute('aria-label') || '').trim().toLowerCase();
            const onclick = (btn.getAttribute('onclick') || '').toLowerCase();
            const isNegative = /\b(disagree|decline|reject|cancel|leave|exit|close window)\b/i.test(txt) ||
                               /\b(disagree|decline|reject|cancel|leave|exit)\b/i.test(onclick);
            if (isNegative) return; // NEVER match or click negative/disagree buttons!

            const isAgree = (/\b(i\s+)?(agree|accept|continue|proceed|acknowledge|dismiss|understand)\b/i.test(txt) ||
                            /close.*alert|dismiss|agree|accept/i.test(onclick)) && !isNegative;
            if (isAgree) {
                const style = window.getComputedStyle(btn);
                if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                    // Find closest modal/dialog or container div/table/form (excluding document.body)
                    const box = btn.closest("[role='dialog'], [role='alertdialog'], div[id], div[class], table, form") || btn.parentElement;
                    if (box && box !== document.body) {
                        foundElements.add(box);
                    }
                }
            }
        } catch(e) {}
    });

    // Also check if the whole frame/body is an agreement page (e.g. agree.html)
    const isAgreeUrl = window.location.href.toLowerCase().includes('agree') || 
                       document.title.toLowerCase().includes('disclaimer') ||
                       document.title.toLowerCase().includes('agreement');
    if (isAgreeUrl && foundElements.size === 0 && document.body) {
        foundElements.add(document.body);
    }

    for (const el of foundElements) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (el !== document.body && (rect.width < 50 || rect.height < 30)) continue;

        const buttons = [];
        const seenBtn = new Set();

        el.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"]').forEach(b => {
            const bStyle = window.getComputedStyle(b);
            if (bStyle.display !== 'none' && bStyle.visibility !== 'hidden') {
                const ariaLabel = (b.getAttribute('aria-label') || '').trim();
                const dt = (b.getAttribute('data-test') || '').trim();
                const rawVal = (b.getAttribute('value') || '').trim();
                const innerTxt = (b.innerText || rawVal || b.title || '').trim();
                const txt = (innerTxt || ariaLabel || dt || 'button').slice(0, 60);

                let sel = '';
                if (b.id && !/^\\d/.test(b.id)) {
                    sel = '#' + CSS.escape(b.id);
                } else if (rawVal && b.tagName.toLowerCase() === 'input') {
                    sel = `input[value=\"${rawVal.replace(/"/g, '\\\\"')}\"]`;
                } else if (ariaLabel) {
                    sel = b.tagName.toLowerCase() + '[aria-label=\"' + ariaLabel.replace(/"/g, '\\\\"') + '\"]';
                } else if (dt) {
                    sel = '[data-test=\"' + dt.replace(/"/g, '\\\\"') + '\"]';
                } else if (b.className && b.className.includes('btn-close')) {
                    sel = b.tagName.toLowerCase() + '.btn-close';
                } else if (innerTxt) {
                    sel = b.tagName.toLowerCase() + ':has-text(\"' + innerTxt.replace(/"/g, '\\\\"') + '\")';
                } else {
                    sel = b.tagName.toLowerCase();
                }

                // Filter negative buttons and external social/marketing links
                const href = (b.getAttribute('href') || '').toLowerCase();
                if (href.includes('facebook.com') || href.includes('twitter.com') || href.includes('x.com') || 
                    href.includes('linkedin.com') || href.includes('instagram.com') || href.includes('youtube.com')) {
                    return;
                }
                const isNegative = /\b(disagree|decline|reject|cancel|leave|exit)\b/i.test(txt);
                if (isNegative) return; // NEVER present negative buttons to the AI or click them

                if (!seenBtn.has(sel)) {
                    seenBtn.add(sel);
                    // Prioritize agreement / dismissal buttons to the top of the candidate button list
                    const isPriority = /\b(i\s+)?(agree|accept|continue|proceed|acknowledge|close|ok)\b/i.test(txt);
                    const btnObj = {
                        tag: b.tagName.toLowerCase(),
                        text: txt,
                        aria_label: ariaLabel,
                        selector: sel
                    };
                    if (isPriority) {
                        buttons.unshift(btnObj);
                    } else {
                        buttons.push(btnObj);
                    }
                }
            }
        });

        if (buttons.length === 0) continue;

        const titleEl = el.querySelector('h1, h2, h3, h4, .title, .ui-dialog-title, .notification-title, [id*=\"title\" i], [class*=\"title\" i], b, strong');
        const title = (titleEl ? titleEl.innerText : (document.title || 'Notice/Disclaimer')).trim().slice(0, 80);
        const textContent = (el.innerText || '').slice(0, 500);

        candidates.push({
            container_id: el.id || '',
            container_class: el.className || '',
            title: title || 'Notice/Disclaimer',
            text_preview: textContent,
            buttons: buttons.slice(0, 10)
        });
    }
    return candidates;
}
"""

PROMPT_TEMPLATE = """You are an automated web browser agent.
Analyze these modal/dialog/disclaimer/notification candidates found on the webpage:
{candidates_json}

Determine if there is an active website disclaimer, terms of use, public notifications popup, privacy notice, or alert modal blocking the page or requiring user acceptance/dismissal.
If YES:
1. Identify the primary acceptance / acknowledge or close button (prefer 'OK', 'I Agree', 'I agree, please continue', 'Accept', 'Continue', 'Proceed', 'Acknowledge', 'Close', or buttons closing the notification popup).
2. Identify the frame_index and provide the exact Playwright selector from the buttons list (or construct a valid CSS/attribute selector like #button1, input[value*='...'], button:has-text('...'), or button.btn-close).
If NO active disclaimer or modal is blocking the user from searching, return has_disclaimer: false.

Return ONLY valid JSON matching this schema:
{{
  "has_disclaimer": true,
  "frame_index": 0,
  "dialog_title": "Website Disclaimer",
  "dismiss_button_text": "I agree, please continue",
  "dismiss_selector": "#button1",
  "reasoning": "Brief explanation"
}}
"""


async def extract_modal_candidates(page: Page) -> list[dict[str, Any]]:
    """Inspects all frames on the page and extracts candidate modals and their interactive buttons."""
    all_candidates: list[dict[str, Any]] = []
    frames = page.frames
    for idx, frame in enumerate(frames):
        try:
            candidates = await frame.evaluate(MODAL_CANDIDATES_JS)
            for c in candidates:
                c["frame_index"] = idx
                c["frame_url"] = frame.url
                all_candidates.append(c)
        except Exception:
            continue
    return all_candidates


async def detect_and_dismiss_modal_ai(
    page: Page,
    *,
    workflow_run_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    AI-driven detection and dismissal of modal disclaimers or terms popups.
    Returns metadata about the detected disclaimer and action taken, suitable for persisting into recipes.
    """
    candidates = await extract_modal_candidates(page)
    if not candidates:
        logger.debug("No modal or disclaimer candidates found on %s", page.url)
        return {"has_disclaimer": False}

    logger.info("Discovered %d modal/disclaimer candidates on %s. Analyzing with AI...", len(candidates), page.url)

    ai_data: dict[str, Any] = {"has_disclaimer": False}

    # 1. Fast-Path Heuristic: Immediately check if any discovered modal has an explicit agreement or acceptance button
    for c in candidates:
        title_text = (c.get("title") or "").lower()
        body_text = (c.get("text_preview") or "").lower()
        is_notice_context = any(
            k in title_text or k in body_text 
            for k in ["disclaimer", "purpose", "informational", "terms", "agree", "notice", "alert", "consent", "warning", "cookie"]
        )
        if is_notice_context or len(c.get("buttons", [])) <= 3:
            for b in c.get("buttons", []):
                b_txt = (b.get("text") or "").strip().lower()
                b_aria = (b.get("aria_label") or "").strip().lower()
                # Exclude negative buttons
                if any(neg in b_txt or neg in b_aria for neg in ["disagree", "decline", "cancel", "reject", "exit", "leave"]):
                    continue
                # Match explicit agreement (ensure not disagree)
                if any(k in b_txt or k in b_aria for k in ["i agree", "agree", "accept", "continue", "proceed", "acknowledge", "understand"]):
                    if "disagree" in b_txt or "disagree" in b_aria:
                        continue
                    ai_data = {
                        "has_disclaimer": True,
                        "frame_index": c.get("frame_index", 0),
                        "dialog_title": c.get("title", "Website Disclaimer"),
                        "dismiss_button_text": b.get("text") or "I agree, please continue",
                        "dismiss_selector": b.get("selector"),
                        "reasoning": f"Fast-path matched agreement button '{b.get('text')}' inside '{c.get('title')}'",
                    }
                    break
        if ai_data.get("has_disclaimer"):
            break

    # 2. AI LLM Detection on full candidates list if fast-path did not detect
    if not ai_data.get("has_disclaimer") and llm_configured():
        try:
            prompt = PROMPT_TEMPLATE.format(candidates_json=json.dumps(candidates, indent=2))
            resp, model_used = await chat_completions_create(
                create_kwargs={
                    "model": getattr(settings, "OPENAI_MODEL", "gpt-6-luna") or "gpt-6-luna",
                    "messages": [
                        {"role": "system", "content": "You are a web automation assistant. Respond strictly in valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
            )
            content = resp.choices[0].message.content or "{}"
            ai_data = json.loads(content)
            logger.info("AI Modal Analysis result (via %s): %s", model_used, ai_data)
        except Exception as e:
            logger.warning("AI disclaimer analysis LLM call failed: %s. Using heuristic fallback.", e)
            ai_data = {"has_disclaimer": False}

    # 3. Fallback Heuristic on Close / Dismiss / OK buttons if LLM returned false
    if not ai_data.get("has_disclaimer"):
        for c in candidates:
            title_text = (c.get("title") or "").lower()
            body_text = (c.get("text_preview") or "").lower()
            if any(k in title_text or k in body_text for k in ["disclaimer", "terms", "agree", "notice", "cookie", "notification", "alert", "warning"]):
                for b in c.get("buttons", []):
                    b_txt = (b.get("text") or "").strip().lower()
                    b_aria = (b.get("aria_label") or "").strip().lower()
                    if any(k in b_txt or k in b_aria for k in ["ok", "i agree", "agree", "accept", "continue", "proceed", "close", "dismiss"]):
                        ai_data = {
                            "has_disclaimer": True,
                            "frame_index": c.get("frame_index", 0),
                            "dialog_title": c.get("title", "Website Disclaimer"),
                            "dismiss_button_text": b.get("text") or "Close",
                            "dismiss_selector": b.get("selector"),
                            "reasoning": f"Heuristic match on '{b.get('text')}' inside '{c.get('title')}'",
                        }
                        break
            if ai_data.get("has_disclaimer"):
                break

    # 4. Direct DOM check for visible modal close buttons
    if not ai_data.get("has_disclaimer"):
        for f_idx, f in enumerate(page.frames):
            try:
                fast_loc = f.locator("button.btn-close:visible, button[aria-label*='close' i]:visible, [data-test*='notifications-close']:visible, input[value*='agree' i]:visible, button:has-text('I Agree'):visible").first
                if await fast_loc.count() > 0 and await fast_loc.is_visible():
                    aria = await fast_loc.get_attribute("aria-label") or await fast_loc.get_attribute("value") or await fast_loc.inner_text() or "Close"
                    ai_data = {
                        "has_disclaimer": True,
                        "frame_index": f_idx,
                        "dialog_title": "Public Notifications / Alert",
                        "dismiss_button_text": aria.strip(),
                        "dismiss_selector": "input[value*='agree' i], button:has-text('I Agree'), button.btn-close, button[aria-label*='close' i], [data-test*='notifications-close']",
                        "reasoning": "Fast-path detection of visible modal action button in frame",
                    }
                    break
            except Exception:
                continue

    if not ai_data.get("has_disclaimer") or not ai_data.get("dismiss_selector"):
        return {"has_disclaimer": False}

    # Execute dismissal across target frame and any duplicate modal frames
    dismiss_selector = ai_data["dismiss_selector"]
    button_text = ai_data.get("dismiss_button_text") or "I agree, please continue"
    dialog_title = ai_data.get("dialog_title") or "Website Disclaimer"
    frame_idx = ai_data.get("frame_index", 0)

    target_frame: Frame = page.main_frame
    if 0 <= frame_idx < len(page.frames):
        target_frame = page.frames[frame_idx]

    logger.info("Clicking AI-identified modal dismiss button '%s' (%s) in frame %s", button_text, dismiss_selector, frame_idx)

    # Optional activity logging via BrowserManager
    try:
        from app.browser.browser_manager import get_browser_manager
        bm = await get_browser_manager()
        await bm.record_step_activity(
            node_id=node_id,
            action="dismiss_modal",
            description=f"AI Modal: Dismissing '{dialog_title}' with '{button_text}'",
            status="RUNNING",
            url=page.url,
        )
    except Exception:
        pass

    dismissed = False
    # Execute dismissal across all frames where this dismiss button or agreement input is present
    seen_frames = set()
    frames_to_check = [target_frame, *page.frames]
    for f in frames_to_check:
        if f in seen_frames:
            continue
        seen_frames.add(f)
        frame_clicked = False
        try:
            for s in [
                dismiss_selector,
                f"input[value*='{button_text}' i]",
                "#button1",
                "input[value*='agree' i]",
                "input[value*='continue' i]",
                f"button:has-text('{button_text}')",
                "button:has-text('I Agree')",
                "button:has-text('Agree')",
                "button:has-text('Accept')",
                "button:has-text('Continue')",
                "button.btn-close",
                "[data-test*='notifications-close']",
            ]:
                try:
                    loc = f.locator(s).first
                    if await loc.count() > 0:
                        try:
                            await loc.evaluate("el => { el.click(); if (typeof el.onclick === 'function') { el.onclick(); } }")
                            frame_clicked = True
                            dismissed = True
                        except Exception:
                            try:
                                await loc.click(force=True, timeout=2000)
                                frame_clicked = True
                                dismissed = True
                            except Exception:
                                pass
                        if frame_clicked:
                            break
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Modal dismissal attempt note: %s", exc)

    await page.wait_for_timeout(1000)

    try:
        from app.browser.browser_manager import get_browser_manager
        bm = await get_browser_manager()
        await bm.record_step_activity(
            node_id=node_id,
            action="dismiss_modal",
            description=f"AI Modal: Successfully dismissed '{dialog_title}' with '{button_text}'",
            status="COMPLETED" if dismissed else "FAILED",
            url=page.url,
        )
    except Exception:
        pass

    # Build universal multi-county resilient selector for recipe persistence
    resilient_selector = f"{dismiss_selector}, input[value*='agree' i], input[value*='continue' i], button:has-text('I Agree'), button:has-text('Agree'), button:has-text('Accept'), button:has-text('Continue'), button.btn-close, [aria-label*='close' i]"

    return {
        "has_disclaimer": True,
        "dialog_title": dialog_title,
        "dismiss_button_text": button_text,
        "dismiss_selector": resilient_selector,
        "description": f"Dismiss '{dialog_title}' modal with '{button_text}'",
        "reasoning": ai_data.get("reasoning", ""),
        "dismissed": dismissed,
    }

