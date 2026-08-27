import { describe, expect, it } from 'vitest'
import { firstRunVisible, nextStep, prevStep } from '../src/lib/firstrun'

describe('first-run step machine', () => {
  it('walks welcome → tips → drop → waiting and clamps at the ends', () => {
    expect(nextStep('welcome')).toBe('tips')
    expect(nextStep('tips')).toBe('drop')
    expect(nextStep('drop')).toBe('waiting')
    expect(nextStep('waiting')).toBe('waiting')
    expect(prevStep('welcome')).toBe('welcome')
    expect(prevStep('tips')).toBe('welcome')
    expect(prevStep('drop')).toBe('tips')
  })
})

describe('firstRunVisible', () => {
  // Loading and genuinely-empty are different states: showing the welcome
  // card during the fetch would flash it at every returning user.
  it('shows only on a loaded, empty library', () => {
    expect(firstRunVisible(0, true)).toBe(false)
    expect(firstRunVisible(0, false)).toBe(true)
    expect(firstRunVisible(3, false)).toBe(false)
  })
})
