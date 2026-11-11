def calculate(a, b, op):
    if op == "+":
        return a + b
    elif op == "-":
        return a - b
    elif op == "*":
        return a * b
    elif op == "/":
        if b == 0:
            return "Ошибка: деление на ноль"
        return a / b
    elif op == "**":
        return a ** b
    elif op == "%":
        if b == 0:
            return "Ошибка: деление на ноль"
        return a % b
    else:
        return "Ошибка: неизвестная операция"


def get_number(prompt):
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print("Ошибка: введите число.")


def main():
    print("Калькулятор. Доступные операции: + - * / ** %")
    print("Для выхода введите q в поле операции.")

    while True:
        a = get_number("Первое число: ")
        op = input("Операция (+ - * / ** % или q): ").strip()

        if op == "q":
            print("Выход.")
            break

        b = get_number("Второе число: ")
        print("Результат:", calculate(a, b, op))
        print()


if __name__ == "__main__":
    main()
