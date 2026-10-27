/**
 * Ready-to-use C / C++ starter programs offered by the template dropdown.
 *
 * Every template is a complete, warning-free submission for the judge
 * (`gcc -O2 -Wall` / `g++ -O2 -std=c++17 -Wall`) and ships with a sample
 * stdin payload, so students see a meaningful run on the first click.
 *
 * `String.raw` keeps the C escapes (`\n`, `%d`) readable: the template text is
 * exactly what appears in the editor.
 */
import type { Language } from './types'

/** Stable identifiers used by the template `<select>`. */
export type TemplateId = 'hello' | 'sum' | 'sort-filter'

export interface CodeTemplate {
  id: TemplateId
  /** Short label shown in the dropdown. */
  label: string
  /** One-line explanation shown next to the editor. */
  description: string
  /** Sample stdin loaded together with the template. */
  stdin: string
  /** Full source file loaded into the editor. */
  code: string
}

const C_TEMPLATES: CodeTemplate[] = [
  {
    id: 'hello',
    label: 'Hello World',
    description: 'Print a greeting to standard output.',
    stdin: '',
    code: String.raw`/*
 * CodingPass starter - Hello, World
 * Writes a greeting to standard output.
 */
#include <stdio.h>

int main(void) {
    printf("Hello, CodingPass!\n");
    return 0;
}
`,
  },
  {
    id: 'sum',
    label: 'Stdin: Sum of Two Numbers',
    description: 'Read two integers with scanf() and print their sum.',
    stdin: '2 3',
    code: String.raw`/*
 * CodingPass starter - Sum of two numbers
 * Reads two integers from standard input and prints their sum.
 */
#include <stdio.h>

int main(void) {
    int a = 0;
    int b = 0;

    if (scanf("%d %d", &a, &b) != 2) {
        fprintf(stderr, "expected two integers on stdin\n");
        return 1;
    }

    printf("%d\n", a + b);
    return 0;
}
`,
  },
  {
    id: 'sort-filter',
    label: 'Stdin: Array Sorting & Filtering',
    description: 'qsort() the input, then print the positive values.',
    stdin: '8\n5 -3 12 0 7 -1 4 9',
    code: String.raw`/*
 * CodingPass starter - Array sorting & filtering
 *
 * stdin : n, followed by n integers
 * stdout: every value sorted ascending, then only the positive ones
 */
#include <stdio.h>
#include <stdlib.h>

#define MAX_N 10240

static int compare_asc(const void *lhs, const void *rhs) {
    int a = *(const int *)lhs;
    int b = *(const int *)rhs;
    return (a > b) - (a < b);
}

int main(void) {
    static int values[MAX_N];
    int n = 0;

    if (scanf("%d", &n) != 1 || n < 0 || n > MAX_N) {
        fprintf(stderr, "expected a count between 0 and %d\n", MAX_N);
        return 1;
    }

    for (int i = 0; i < n; ++i) {
        if (scanf("%d", &values[i]) != 1) {
            fprintf(stderr, "expected %d integers\n", n);
            return 1;
        }
    }

    qsort(values, (size_t)n, sizeof values[0], compare_asc);

    printf("sorted:   ");
    for (int i = 0; i < n; ++i) {
        printf("%d ", values[i]);
    }
    printf("\n");

    printf("positive: ");
    for (int i = 0; i < n; ++i) {
        if (values[i] > 0) {
            printf("%d ", values[i]);
        }
    }
    printf("\n");

    return 0;
}
`,
  },
]

const CPP_TEMPLATES: CodeTemplate[] = [
  {
    id: 'hello',
    label: 'Hello World',
    description: 'Print a greeting with std::cout.',
    stdin: '',
    code: String.raw`//
// CodingPass starter - Hello, World
// Writes a greeting to standard output.
//
#include <iostream>

int main() {
    std::cout << "Hello, CodingPass!" << std::endl;
    return 0;
}
`,
  },
  {
    id: 'sum',
    label: 'Stdin: Sum of Two Numbers',
    description: 'Read two integers with std::cin and print their sum.',
    stdin: '2 3',
    code: String.raw`//
// CodingPass starter - Sum of two numbers
// Reads two integers from standard input and prints their sum.
//
#include <iostream>

int main() {
    int a = 0;
    int b = 0;

    if (!(std::cin >> a >> b)) {
        std::cerr << "expected two integers on stdin\n";
        return 1;
    }

    std::cout << (a + b) << '\n';
    return 0;
}
`,
  },
  {
    id: 'sort-filter',
    label: 'Stdin: Array Sorting & Filtering',
    description: 'std::sort the input, then print the positive values.',
    stdin: '8\n5 -3 12 0 7 -1 4 9',
    code: String.raw`//
// CodingPass starter - Array sorting & filtering
//
// stdin : n, followed by n integers
// stdout: every value sorted ascending, then only the positive ones
//
#include <algorithm>
#include <iostream>
#include <iterator>
#include <vector>

int main() {
    int n = 0;

    if (!(std::cin >> n) || n < 0) {
        std::cerr << "expected a non-negative count\n";
        return 1;
    }

    std::vector<int> values;
    values.reserve(static_cast<std::size_t>(n));

    for (int i = 0; i < n; ++i) {
        int x = 0;
        if (!(std::cin >> x)) {
            std::cerr << "expected " << n << " integers\n";
            return 1;
        }
        values.push_back(x);
    }

    std::sort(values.begin(), values.end());

    std::vector<int> positives;
    std::copy_if(values.begin(), values.end(), std::back_inserter(positives),
                 [](int x) { return x > 0; });

    std::cout << "sorted:   ";
    for (int x : values) {
        std::cout << x << ' ';
    }

    std::cout << "\npositive: ";
    for (int x : positives) {
        std::cout << x << ' ';
    }
    std::cout << '\n';

    return 0;
}
`,
  },
]

const TEMPLATES: Record<Language, CodeTemplate[]> = {
  c: C_TEMPLATES,
  cpp: CPP_TEMPLATES,
}

/** Template shown when the UI starts up. */
export const DEFAULT_TEMPLATE_ID: TemplateId = 'hello'

/** Templates available for a language, in dropdown order. */
export function templatesFor(language: Language): CodeTemplate[] {
  return TEMPLATES[language]
}

/** Look up a single template. */
export function getTemplate(language: Language, id: TemplateId): CodeTemplate | undefined {
  return templatesFor(language).find((template) => template.id === id)
}

/** Source of the default template for a language. */
export function defaultSource(language: Language): string {
  return getTemplate(language, DEFAULT_TEMPLATE_ID)!.code
}

/**
 * Reverse lookup used by the dropdown: returns the template id whose code is
 * still untouched in the editor, or `''` for student-written code.
 */
export function findTemplateId(language: Language, code: string): string {
  return templatesFor(language).find((template) => template.code === code)?.id ?? ''
}
