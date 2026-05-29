
import sys
sys.stdout.write(f'STDOUT: {type(sys.stdout).__name__} id={id(sys.stdout)}\n')
sys.stdout.write(f'__STDOUT__: {type(sys.__stdout__).__name__} id={id(sys.__stdout__)}\n')
sys.stdout.write(f'SAME: {sys.stdout is sys.__stdout__}\n')
sys.stdout.flush()

def test_check():
    import sys
    # In test context
    sys.stdout.write(f'IN_TEST STDOUT: {type(sys.stdout).__name__} id={id(sys.stdout)}\n')
    sys.stdout.write(f'IN_TEST __STDOUT__: {type(sys.__stdout__).__name__} id={id(sys.__stdout__)}\n')
    sys.stdout.write(f'IN_TEST SAME: {sys.stdout is sys.__stdout__}\n')
    sys.stdout.flush()
    assert True
