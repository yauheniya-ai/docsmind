import { useEffect, useState } from "react"

const TYPE_MS = 22
const DELETE_MS = 11
const HOLD_MS = 1800 // a finished phrase stays readable
const GAP_MS = 350 // pause between erasing one phrase and typing the next
const REDUCED_MOTION_MS = 4000

/** Types each phrase out, holds it, erases it, then moves to the next, forever.
 * Returns the text to show right now ("" while `enabled` is false).
 *
 * `phrases` must be a stable reference (module constant or memoized), since
 * a new array restarts the animation. With reduced motion the phrases are
 * swapped whole, without the typing effect.
 */
export function useTypewriter(phrases: readonly string[], enabled: boolean): string {
  const [text, setText] = useState("")

  useEffect(() => {
    if (!enabled || phrases.length === 0) return

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    let phraseIndex = 0
    let length = 0
    let deleting = false
    let timer: ReturnType<typeof setTimeout>

    const tick = () => {
      const phrase = phrases[phraseIndex]

      if (reduced) {
        setText(phrase)
        phraseIndex = (phraseIndex + 1) % phrases.length
        timer = setTimeout(tick, REDUCED_MOTION_MS)
        return
      }

      if (!deleting) {
        length += 1
        setText(phrase.slice(0, length))
        if (length >= phrase.length) {
          deleting = true
          timer = setTimeout(tick, HOLD_MS)
        } else {
          timer = setTimeout(tick, TYPE_MS)
        }
      } else {
        length -= 1
        setText(phrase.slice(0, length))
        if (length <= 0) {
          deleting = false
          phraseIndex = (phraseIndex + 1) % phrases.length
          timer = setTimeout(tick, GAP_MS)
        } else {
          timer = setTimeout(tick, DELETE_MS)
        }
      }
    }

    timer = setTimeout(tick, GAP_MS)
    return () => clearTimeout(timer)
  }, [phrases, enabled])

  return enabled ? text : ""
}
