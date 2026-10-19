"""Rasch / Bradley-Terry rating over every trial of every benchmark, retired ones included.

    P(pass) = logistic(skill[recipe] - difficulty[bench, task])

Fitted jointly by maximum a posteriori (Newton's method on all parameters at once) with Gaussian
priors, so recipes that ran only part of the tasks, or passed / failed everything, still get a finite
rating with a wider interval. Tasks are items per bench: the same upstream task in two benches is two
items, because the settings differ. The result does not depend on the order results arrived in.

Rating = 1000 + 400 / ln(10) * skill (the Elo scale: +100 means ~64% vs 36% odds on the same task).
"""
import math

ELO = 400 / math.log(10)
SKILL_SD, DIFFICULTY_SD = 1.5, 2.0


def _solve(a, b):
    """Solve a x = b by Gauss-Jordan with partial pivoting; also return a^-1 diagonal."""
    n = len(a)
    m = [row[:] + [b[i]] + [1.0 if j == i else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        m[c] = [v / piv for v in m[c]]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[c])]
    x = [m[i][n] for i in range(n)]
    inv_diag = [m[i][n + 1 + i] for i in range(n)]
    return x, inv_diag


def fit(observations, iters=50, tol=1e-9):
    """observations: iterable of (recipe, item, reward in [0, 1]), one per trial.

    Returns {recipe: (rating, half_width_95)}.
    """
    cells = {}
    for recipe, item, reward in observations:
        n, s = cells.get((recipe, item), (0, 0.0))
        cells[(recipe, item)] = (n + 1, s + min(max(float(reward), 0.0), 1.0))
    recipes = sorted({r for r, _ in cells})
    items = sorted({i for _, i in cells})
    if not recipes:
        return {}
    idx = {("r", r): k for k, r in enumerate(recipes)}
    idx.update({("i", i): len(recipes) + k for k, i in enumerate(items)})
    n_par = len(idx)
    prec = [1 / SKILL_SD ** 2] * len(recipes) + [1 / DIFFICULTY_SD ** 2] * len(items)
    theta = [0.0] * n_par

    inv_diag = [1 / p for p in prec]
    for _ in range(iters):
        grad = [-prec[k] * theta[k] for k in range(n_par)]
        hess = [[0.0] * n_par for _ in range(n_par)]
        for k in range(n_par):
            hess[k][k] = prec[k]
        for (recipe, item), (n, s) in cells.items():
            a, b = idx[("r", recipe)], idx[("i", item)]
            p = 1 / (1 + math.exp(-(theta[a] - theta[b])))
            g, w = s - n * p, n * p * (1 - p)
            grad[a] += g
            grad[b] -= g
            hess[a][a] += w
            hess[b][b] += w
            hess[a][b] -= w
            hess[b][a] -= w
        step, inv_diag = _solve(hess, grad)
        theta = [t + d for t, d in zip(theta, step)]
        if max(abs(d) for d in step) < tol:
            break
    return {r: (1000 + ELO * theta[idx[("r", r)]], 1.96 * ELO * math.sqrt(inv_diag[idx[("r", r)]]))
            for r in recipes}
