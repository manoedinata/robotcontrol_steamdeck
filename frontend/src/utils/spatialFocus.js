const DIRECTIONS = ['up', 'down', 'left', 'right']

function centerOf(element) {
    const rect = element.getBoundingClientRect()
    return {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
    }
}

export function isDirection(action) {
    return DIRECTIONS.includes(action)
}

export function focusInDirection(elements, current, direction, options = {}) {
    if (!elements.length) return null
    if (!elements.includes(current)) {
        elements[0].focus(options.focusOptions)
        return elements[0]
    }

    const origin = centerOf(current)
    const candidates = elements.filter((element) => {
        if (element === current) return false
        const point = centerOf(element)
        if (direction === 'up') return point.y < origin.y - 4
        if (direction === 'down') return point.y > origin.y + 4
        if (direction === 'left') return point.x < origin.x - 4
        return point.x > origin.x + 4
    })

    const next = candidates.sort((a, b) => {
        const score = (element) => {
            const point = centerOf(element)
            const vertical = direction === 'up' || direction === 'down'
            const primary = vertical ? Math.abs(point.y - origin.y) : Math.abs(point.x - origin.x)
            const secondary = vertical ? Math.abs(point.x - origin.x) : Math.abs(point.y - origin.y)
            return primary + secondary * 2
        }
        return score(a) - score(b)
    })[0]

    next?.focus(options.focusOptions)
    if (next && options.scrollIntoView) {
        next.scrollIntoView(options.scrollIntoView)
    }
    return next ?? null
}