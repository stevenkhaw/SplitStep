import { describe, expect, it } from 'vitest'
import { isEditableTarget } from '../src/lib/keyboard'

describe('isEditableTarget', () => {
  it('is false for null (svelte:window handlers can fire with no target set)', () => {
    expect(isEditableTarget(null)).toBe(false)
  })

  it('is false for a plain element', () => {
    expect(isEditableTarget(document.createElement('div'))).toBe(false)
  })

  it('is true for an <input> (e.g. QuadEditor\'s preset-name field)', () => {
    expect(isEditableTarget(document.createElement('input'))).toBe(true)
  })

  it('is true for a range input specifically (the threshold sliders)', () => {
    const el = document.createElement('input')
    el.type = 'range'
    expect(isEditableTarget(el)).toBe(true)
  })

  it('is true for a <textarea>', () => {
    expect(isEditableTarget(document.createElement('textarea'))).toBe(true)
  })

  it('is true for a <select>', () => {
    expect(isEditableTarget(document.createElement('select'))).toBe(true)
  })

  it('is true for any contenteditable element, not just form controls', () => {
    const el = document.createElement('div')
    // jsdom does not implement the contenteditable algorithm -- unlike
    // every real browser, `isContentEditable` there is always `undefined`
    // regardless of the attribute (verified directly: setting either the
    // `contentEditable` property or the `contenteditable` attribute leaves
    // it `undefined`). Stubbing the property is the same class of jsdom-
    // compatibility shim requeue-on-detail-swap.test.ts already uses for
    // HTMLMediaElement.prototype.play/pause -- an environment gap, not a
    // workaround for a bug in isEditableTarget.
    Object.defineProperty(el, 'isContentEditable', { value: true })
    expect(isEditableTarget(el)).toBe(true)
  })

  it('is false for a button -- native activation is not typed input', () => {
    expect(isEditableTarget(document.createElement('button'))).toBe(false)
  })

  it('is false for a non-Element EventTarget (e.g. window itself)', () => {
    expect(isEditableTarget(window)).toBe(false)
  })
})
