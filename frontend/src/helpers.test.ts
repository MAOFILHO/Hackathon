import { describe, expect, it } from 'vitest'
import { greetingFor, showAllLabel } from './helpers'

describe('greetingFor', () => {
  it('follows the hour and uses the first name', () => {
    expect(greetingFor(9, 'Ravi Kumar')).toBe('Good morning, Ravi')
    expect(greetingFor(13, 'Ravi')).toBe('Good afternoon, Ravi')
    expect(greetingFor(20, 'Ravi')).toBe('Good evening, Ravi')
  })
  it('shows no name when the backend sends none', () => {
    expect(greetingFor(9)).toBe('Good morning')
    expect(greetingFor(9, '')).toBe('Good morning')
    expect(greetingFor(9, '   ')).toBe('Good morning')
  })
})

describe('showAllLabel', () => {
  it('offers every row when the rest fits under the limit', () => {
    expect(showAllLabel(50, 225)).toBe('Show all 225 rows')
  })
  it('offers the first 1000 rows for a bigger Document, then stops offering', () => {
    expect(showAllLabel(50, 3000)).toBe('Show first 1000 rows')
    expect(showAllLabel(1000, 3000)).toBeNull()
  })
  it('offers nothing when every row is already shown', () => {
    expect(showAllLabel(139, 139)).toBeNull()
    expect(showAllLabel(0, 0)).toBeNull()
  })
})
